#!/usr/bin/env bash
# Create a ready-to-import WireGuard phone profile (config file + QR code) for a faculty member.
#
# The keypair is generated here because the faculty member only scans a QR code in the WireGuard app
# (iOS/Android) and never handles keys. Consequences, handled by this script:
#   * The private key exists only in the output directory (root, mode 700/600) until you delete it.
#   * The profile is split-tunnel to 10.66.0.1/32 only: the phone reaches the Project D server
#     (Team Dashboard, Grafana) and nothing else on the VPN or the internet goes through it.
#   * The peer is registered with vpn/add-peer.sh (dry run unless --apply).
#
# Usage:
#   sudo vpn/make-phone-profile.sh --name faculty-pl --ip 10.66.0.32 --endpoint <server-host>:51820 [--apply] [--show]
# Options:
#   --server-public-key KEY  use KEY instead of reading it from `wg show wg0 public-key`
#   --out DIR                output directory (default /root/projectd-profiles)
#   --show                   also print the QR code in the terminal (for scanning from the screen in person)
# Hand-over: show the QR in person, or send the PNG through a private channel the person agrees to.
# Never post it on Discord or in the repository. Delete the output afterwards: shred -u <dir>/*
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
NAME="" IP="" ENDPOINT="" SERVER_PUB="" OUT="/root/projectd-profiles" APPLY=0 SHOW=0
usage() { sed -n '2,19p' "$0"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="${2:-}"; shift 2 ;;
    --ip) IP="${2:-}"; shift 2 ;;
    --endpoint) ENDPOINT="${2:-}"; shift 2 ;;
    --server-public-key) SERVER_PUB="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --show) SHOW=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$NAME" && -n "$IP" && -n "$ENDPOINT" ]] || { usage >&2; exit 2; }
[[ "$NAME" =~ ^[a-z0-9][a-z0-9-]{1,31}$ ]] || { echo "Name: 2-32 lower-case letters, digits or hyphens." >&2; exit 2; }
[[ "$IP" =~ ^10\.66\.0\.([0-9]{1,3})$ ]] && (( BASH_REMATCH[1] >= 2 && BASH_REMATCH[1] <= 254 )) || {
  echo "IP must be inside 10.66.0.0/24 (and not .0, .1 or .255)." >&2; exit 2; }
[[ "$ENDPOINT" =~ ^[A-Za-z0-9.-]+:[0-9]{1,5}$ ]] || { echo "Endpoint must be host:port, e.g. vpn.example.org:51820" >&2; exit 2; }
for tool in wg qrencode; do
  command -v "$tool" >/dev/null || { echo "$tool is required (apt install wireguard-tools qrencode)." >&2; exit 1; }
done
case "$IP" in
  10.66.0.31|10.66.0.32) ;;
  *) echo "NOTE: $IP is not a faculty address (.31/.32 in documents/vpn-plan.md); check the address plan." >&2 ;;
esac

if [[ -z "$SERVER_PUB" ]]; then
  SERVER_PUB="$(wg show wg0 public-key 2>/dev/null)" || {
    echo "Cannot read the server key (run as root on the VPN server, or pass --server-public-key)." >&2; exit 1; }
fi
[[ "$SERVER_PUB" =~ ^[A-Za-z0-9+/]{43}=$ ]] || { echo "Server public key looks invalid." >&2; exit 2; }

umask 077
DIR="$OUT/$NAME"
if [[ -e "$DIR/$NAME.conf" ]]; then
  echo "$DIR/$NAME.conf already exists. Delete it (shred -u) before creating a new key for $NAME." >&2; exit 1
fi
mkdir -p "$DIR"
chmod 700 "$OUT" "$DIR"

PRIVATE_KEY="$(wg genkey)"
PUBLIC_KEY="$(printf '%s' "$PRIVATE_KEY" | wg pubkey)"

# Register the peer first; if that fails, no profile with a dangling key is left behind.
REGISTER=(bash "$SCRIPT_DIR/add-peer.sh" --name "$NAME" --ip "$IP" --public-key "$PUBLIC_KEY")
[[ "$APPLY" == 1 ]] && REGISTER+=(--apply)
"${REGISTER[@]}"

CONF="$DIR/$NAME.conf"
cat > "$CONF" <<EOF
# Project D VPN profile for $NAME ($IP). Personal: do not forward or share.
[Interface]
PrivateKey = $PRIVATE_KEY
Address = $IP/32
MTU = 1420

[Peer]
PublicKey = $SERVER_PUB
Endpoint = $ENDPOINT
# Split tunnel: only the Project D server (Team Dashboard, Grafana) goes through the VPN.
AllowedIPs = 10.66.0.1/32
PersistentKeepalive = 25
EOF
unset PRIVATE_KEY
chmod 600 "$CONF"
qrencode -t png -o "$DIR/$NAME.png" -r "$CONF"
chmod 600 "$DIR/$NAME.png"

echo
echo "Profile for $NAME ($IP): $CONF and $DIR/$NAME.png (root only)."
echo "Peer public key: $PUBLIC_KEY"
if [[ "$APPLY" != 1 ]]; then
  echo "DRY RUN: the peer was NOT added to wg0. Re-run with --apply after deleting this output, or the key will not work."
fi
if [[ "$SHOW" == 1 ]]; then
  qrencode -t ansiutf8 -r "$CONF"
fi
cat <<EOF

Hand-over: in the WireGuard app choose "Create from QR code" (Android: + > Scan from QR code) and scan.
Test: open http://10.66.0.1:8088 on the phone with the tunnel on.
Afterwards delete the private material:  shred -u "$CONF" "$DIR/$NAME.png" && rmdir "$DIR"
EOF
