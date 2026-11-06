#!/usr/bin/env bash
# Print the Project D UFW policy by default; change nothing without --apply.
#
# Safety rules:
# - Allow rules are added BEFORE the default-deny policy.
# - If UFW is already ACTIVE, `ufw default deny incoming` takes effect immediately and
#   new SSH logins over the public IP stop working. The script then refuses to apply
#   unless --confirm-vpn-ssh is given, i.e. you have already logged in via 10.66.0.1
#   in a second session AND tested the provider's emergency console.
# - The script never runs `ufw enable` and never changes sshd's ListenAddress.
set -Eeuo pipefail

APPLY=0
CONFIRM_VPN_SSH=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --dry-run) APPLY=0 ;;
    --confirm-vpn-ssh) CONFIRM_VPN_SSH=1 ;;
    -h|--help)
      echo "Usage: $0 [--dry-run|--apply] [--confirm-vpn-ssh]"
      exit 0 ;;
    *) echo "Usage: $0 [--dry-run|--apply] [--confirm-vpn-ssh]" >&2; exit 2 ;;
  esac
done

# Order matters: allow rules first, default policies last.
COMMANDS=(
  "ufw allow 51820/udp"
  "ufw allow in on wg0 from 10.66.0.11 to 10.66.0.1 port 22 proto tcp"
  "ufw allow in on wg0 from 10.66.0.12 to 10.66.0.1 port 22 proto tcp"
  "ufw allow in on wg0 from 10.66.0.21 to 10.66.0.1 port 22 proto tcp"
  "ufw allow in on wg0 from 10.66.0.22 to 10.66.0.1 port 22 proto tcp"
  "ufw default allow outgoing"
  "ufw default deny incoming"
)

echo "Planned commands:"
for command in "${COMMANDS[@]}"; do
  printf '  sudo %s\n' "$command"
done
cat <<'EOF'
Not done by this script: `ufw enable`, sshd ListenAddress changes, Docker port rules
(see firewall/docker-user.sh) or backend/MQTT/dashboard ports.
EOF

if [[ "$APPLY" == "0" ]]; then
  echo "Dry run — nothing changed."
  exit 0
fi

[[ "${EUID}" -eq 0 ]] || { echo "Run as root for --apply." >&2; exit 1; }
command -v ufw >/dev/null || { echo "ufw is not installed." >&2; exit 1; }
wg show wg0 >/dev/null 2>&1 || { echo "wg0 is not active; refusing to restrict remote access." >&2; exit 1; }

if ufw status | grep -q "^Status: active"; then
  if [[ "$CONFIRM_VPN_SSH" != "1" ]]; then
    cat >&2 <<'EOF'
UFW is ALREADY ACTIVE: applying `default deny incoming` blocks new public SSH logins at once.
Before re-running with --apply --confirm-vpn-ssh:
  1. Open a second SSH session to 10.66.0.1 over WireGuard and keep it open.
  2. Confirm the provider's emergency/VNC console works.
No rules were changed.
EOF
    exit 1
  fi
  echo "UFW is active; --confirm-vpn-ssh given, applying."
fi

for command in "${COMMANDS[@]}"; do
  # The command strings contain only the reviewed static rules above.
  $command
done

if ufw status | grep -q "^Status: active"; then
  echo "UFW rules applied and active. Verify SSH via 10.66.0.1 in a NEW session before closing the old one."
else
  echo "UFW rules staged (UFW inactive). Enable only after verifying SSH via 10.66.0.1 and the emergency console."
fi
