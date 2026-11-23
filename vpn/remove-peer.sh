#!/usr/bin/env bash
# Revoke one WireGuard peer (lost device, leaked private key) without restarting wg0.
#
# Removes the [Peer] block that add-peer.sh wrote for <name> from wg0.conf, applies it with
# `wg syncconf`, and marks the registry row "Revoked" so the name and IP can be enrolled again
# with a NEW key (vpn/add-peer.sh or vpn/make-phone-profile.sh).
#
# Usage:
#   sudo vpn/remove-peer.sh --name <peer-name> [--apply]
# Defaults to a dry run. Environment overrides:
#   WG_CONFIG=/etc/wireguard/wg0.conf  WG_INTERFACE=wg0  PEER_REGISTRY=vpn/peers.md
set -Eeuo pipefail

CONFIG_FILE="${WG_CONFIG:-/etc/wireguard/wg0.conf}"
INTERFACE="${WG_INTERFACE:-wg0}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${PEER_REGISTRY:-${SCRIPT_DIR}/peers.md}"
NAME="" APPLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="${2:-}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --dry-run) APPLY=0; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "$NAME" =~ ^[a-z0-9][a-z0-9-]{1,31}$ ]] || { echo "--name: 2-32 lower-case letters, digits or hyphens." >&2; exit 2; }
[[ -r "$CONFIG_FILE" ]] || { echo "Cannot read $CONFIG_FILE" >&2; exit 1; }

# Print the config without the peer block whose first comment line is "# <name>"; exit 3 if there is none.
strip_peer() {
  python3 - "$CONFIG_FILE" "$NAME" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
name = sys.argv[2]
# A section runs from its "[...]" header to the next header or the end of the file.
parts = re.split(r"(?m)^(?=\[)", text)
kept, removed = [], []
for part in parts:
    lines = part.splitlines()
    is_target = (lines and lines[0].strip() == "[Peer]" and len(lines) > 1 and lines[1].strip() == f"# {name}")
    (removed if is_target else kept).append(part)
if len(removed) != 1:
    sys.stderr.write(f"Expected exactly one [Peer] block named {name}, found {len(removed)}.\n")
    sys.exit(3)
for line in removed[0].splitlines():
    if line.strip().startswith(("PublicKey", "AllowedIPs")):
        sys.stderr.write("  remove: " + line.strip() + "\n")
sys.stdout.write("".join(kept).rstrip("\n") + "\n")
PY
}

NEW_CONFIG="$(strip_peer)" || exit $?
if [[ "$APPLY" == 0 ]]; then
  echo "Dry run - no changes made. Re-run with --apply to revoke $NAME."
  exit 0
fi
[[ "${EUID}" -eq 0 ]] || { echo "Run as root with --apply." >&2; exit 1; }

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="${CONFIG_FILE}.before-remove-${NAME}-${TIMESTAMP}"
install -m 600 "$CONFIG_FILE" "$BACKUP"
umask 077
printf '%s\n' "$NEW_CONFIG" > "$CONFIG_FILE"
chmod 600 "$CONFIG_FILE"
if ! wg syncconf "$INTERFACE" <(wg-quick strip "$CONFIG_FILE"); then
  echo "syncconf failed; restoring $BACKUP" >&2
  install -m 600 "$BACKUP" "$CONFIG_FILE"
  wg syncconf "$INTERFACE" <(wg-quick strip "$CONFIG_FILE") || true
  exit 1
fi

if [[ -w "$REGISTRY" ]]; then
  python3 - "$REGISTRY" "$NAME" "$TIMESTAMP" <<'PY' || echo "Peer revoked, but registry update failed: $REGISTRY" >&2
import sys
from pathlib import Path
path, name, stamp = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines()
prefix = f"| `{name}` |"
for i, line in enumerate(lines):
    if line.startswith(prefix):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 7:
            cells[5] = "Revoked"
            cells[6] = f"Revoked {stamp} by remove-peer.sh"
            lines[i] = "| " + " | ".join(cells) + " |"
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
fi
echo "Peer $NAME revoked without restarting $INTERFACE. Backup: $BACKUP"
