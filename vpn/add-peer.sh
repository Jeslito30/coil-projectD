#!/usr/bin/env bash
# Add one WireGuard peer without restarting wg0 or dropping existing peers.
set -Eeuo pipefail

CONFIG_FILE="${WG_CONFIG:-/etc/wireguard/wg0.conf}"
INTERFACE="${WG_INTERFACE:-wg0}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${PEER_REGISTRY:-${SCRIPT_DIR}/peers.md}"
APPLY=0
NAME=""
IP=""
PUBLIC_KEY=""

usage() {
  cat <<'EOF'
Usage:
  sudo vpn/add-peer.sh --name <peer-name> --ip <10.66.0.x> --public-key <base64-key> [--apply]

Defaults to a dry run. Environment overrides:
  WG_CONFIG=/etc/wireguard/wg0.conf  WG_INTERFACE=wg0  PEER_REGISTRY=vpn/peers.md

The script adds a [Peer] block, uses `wg syncconf`, and appends a public-only
registry entry. It does not create client keys or modify firewall rules.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="${2:-}"; shift 2 ;;
    --ip) IP="${2:-}"; shift 2 ;;
    --public-key) PUBLIC_KEY="${2:-}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --dry-run) APPLY=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$NAME" || -z "$IP" || -z "$PUBLIC_KEY" ]]; then
  usage >&2
  exit 2
fi
[[ "$NAME" =~ ^[a-z0-9][a-z0-9-]{1,31}$ ]] || {
  echo "Peer name must be 2–32 lower-case letters, digits or hyphens." >&2; exit 2;
}
[[ "$PUBLIC_KEY" =~ ^[A-Za-z0-9+/]{43}=$ ]] || {
  echo "WireGuard public key must be 44-character base64 ending in '='." >&2; exit 2;
}
DECODED_LENGTH="$(printf '%s' "$PUBLIC_KEY" | base64 -d 2>/dev/null | wc -c | tr -d ' ')" || {
  echo "Invalid base64 public key." >&2; exit 2;
}
[[ "$DECODED_LENGTH" == "32" ]] || {
  echo "WireGuard public key must decode to 32 bytes." >&2; exit 2;
}
python3 - "$IP" <<'PY'
import ipaddress
import sys
try:
    address = ipaddress.ip_address(sys.argv[1])
except ValueError as exc:
    raise SystemExit(f"Invalid VPN IP: {exc}")
network = ipaddress.ip_network("10.66.0.0/24")
if address not in network or address in {network.network_address, network.broadcast_address, ipaddress.ip_address("10.66.0.1")}:
    raise SystemExit("IP must be an unassigned host in 10.66.0.0/24, excluding .0, .1 and .255")
PY

# Registry helper: "check" verifies that a reserved row (if any) uses the same IP;
# "write" turns that row into an Active entry, or appends a new row if none exists.
registry() {
  python3 - "$1" "$REGISTRY" "$NAME" "$IP" "$PUBLIC_KEY" "${2:-}" <<'PY'
import sys
from pathlib import Path

mode, path, name, ip, key, stamp = sys.argv[1:7]
registry = Path(path)
if not registry.exists():
    sys.exit(0 if mode == "check" else f"Registry not found: {registry}")
lines = registry.read_text(encoding="utf-8").splitlines()
prefix = f"| `{name}` |"
matches = [i for i, line in enumerate(lines) if line.startswith(prefix)]
if len(matches) > 1:
    sys.exit(f"Registry has {len(matches)} rows for {name}; fix them by hand first.")
for i, line in enumerate(lines):
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) == 7 and cells[2] == f"`{ip}`" and i not in matches:
        sys.exit(f"Registry already assigns {ip} to {cells[0]}.")
if matches:
    cells = [c.strip() for c in lines[matches[0]].strip().strip("|").split("|")]
    if len(cells) != 7:
        sys.exit(f"Unexpected registry row format for {name}.")
    if cells[2] != f"`{ip}`":
        sys.exit(f"Registry reserves {name} for {cells[2]}, not {ip}. Use the reserved address.")
    if cells[5] == "Active":
        sys.exit(f"{name} is already Active in the registry. Revoke it first.")
if mode == "check":
    sys.exit(0)
if matches:
    cells[3], cells[4], cells[5] = f"`{key}`", f"`{stamp}`", "Active"
    lines[matches[0]] = "| " + " | ".join(cells) + " |"
else:
    lines.append(f"| `{name}` | Custodian TBD | `{ip}` | `{key}` | `{stamp}` | Active | Added by add-peer.sh |")
registry.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

registry check || exit 1

[[ -r "$CONFIG_FILE" ]] || { echo "Cannot read $CONFIG_FILE" >&2; exit 1; }
if grep -F "$PUBLIC_KEY" "$CONFIG_FILE" >/dev/null; then
  echo "Public key already exists in $CONFIG_FILE" >&2; exit 1
fi
if grep -E "^[[:space:]]*AllowedIPs[[:space:]]*=[[:space:]]*${IP}/32([[:space:]]|$)" "$CONFIG_FILE" >/dev/null; then
  echo "AllowedIPs $IP/32 already exists in $CONFIG_FILE" >&2; exit 1
fi

if [[ "$APPLY" == "0" ]]; then
  cat <<EOF
Dry run — no changes made.
Would add to $CONFIG_FILE:
[Peer]
# $NAME
PublicKey  = $PUBLIC_KEY
AllowedIPs = $IP/32
EOF
  exit 0
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root with --apply to change WireGuard, or omit --apply for a dry run." >&2
  exit 1
fi

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="${CONFIG_FILE}.before-${NAME}-${TIMESTAMP}"
install -m 600 "$CONFIG_FILE" "$BACKUP"
printf '\n[Peer]\n# %s\nPublicKey  = %s\nAllowedIPs = %s/32\n' \
  "$NAME" "$PUBLIC_KEY" "$IP" >> "$CONFIG_FILE"

if ! wg syncconf "$INTERFACE" < <(wg-quick strip "$CONFIG_FILE"); then
  echo "syncconf failed; restoring $BACKUP" >&2
  install -m 600 "$BACKUP" "$CONFIG_FILE"
  wg syncconf "$INTERFACE" < <(wg-quick strip "$CONFIG_FILE") || true
  exit 1
fi

if [[ -w "$REGISTRY" ]]; then
  registry write "$TIMESTAMP" || echo "Peer applied, but registry update failed: $REGISTRY" >&2
else
  echo "Peer applied, but registry is not writable: $REGISTRY" >&2
fi
echo "Commit the updated $REGISTRY (public key only) to the repository."

echo "Peer $NAME ($IP/32) applied without restarting $INTERFACE. Backup: $BACKUP"
echo "Review firewall access separately; WireGuard enrolment alone grants no service entitlement."
