#!/usr/bin/env bash
# Project D - give one team member access to the standalone server (after deploy.sh).
#
# Three independent kinds of access; pass only what the member needs:
#   VPN      --wg-ip 10.66.0.x --wg-pubkey <key>   WireGuard peer. The member generates the key pair on their
#                                                  own device and sends ONLY the public key (`wg pubkey` output).
#   Grafana  (always)                              personal account, role --grafana-role (default Editor).
#                                                  A random password goes to a root-only file, never to stdout.
#   SSH      --ssh-key "ssh-ed25519 AAAA... name"  personal Linux account with key-only login.
#            --sudo                                also full root via sudo. sudo (and the docker group, which is
#                                                  root-equivalent) is never granted without this flag.
#
# Usage (as root):
#   bash deployment/standalone/add-member.sh --name jeslito --wg-ip 10.66.0.10 --wg-pubkey <key> \
#        [--ssh-key "ssh-ed25519 AAAA..."] [--sudo] [--grafana-role Viewer|Editor|Admin] [--endpoint <public-ip>]
# Re-running is safe: an existing peer, Linux user or Grafana user is kept and only brought in line.
set -Eeuo pipefail

NAME="" WG_IP="" WG_PUB="" SSH_KEY="" SUDO=0 ROLE="Editor" ENDPOINT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="${2:-}"; shift 2 ;;
    --wg-ip) WG_IP="${2:-}"; shift 2 ;;
    --wg-pubkey) WG_PUB="${2:-}"; shift 2 ;;
    --ssh-key) SSH_KEY="${2:-}"; shift 2 ;;
    --sudo) SUDO=1; shift ;;
    --grafana-role) ROLE="${2:-}"; shift 2 ;;
    --endpoint) ENDPOINT="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ "$(id -u)" == 0 ]] || { echo "Run as root." >&2; exit 1; }
[[ "$NAME" =~ ^[a-z][a-z0-9-]{1,30}$ ]] || { echo "--name: 2-31 lower-case letters, digits or hyphens, starting with a letter." >&2; exit 2; }
[[ "$ROLE" =~ ^(Viewer|Editor|Admin)$ ]] || { echo "--grafana-role must be Viewer, Editor or Admin." >&2; exit 2; }
if [[ -n "$WG_IP" || -n "$WG_PUB" ]]; then
  [[ -n "$WG_IP" && -n "$WG_PUB" ]] || { echo "--wg-ip and --wg-pubkey go together." >&2; exit 2; }
fi
if [[ "$SUDO" == 1 && -z "$SSH_KEY" ]]; then echo "--sudo needs --ssh-key." >&2; exit 2; fi
if [[ -n "$SSH_KEY" ]]; then
  [[ "$SSH_KEY" =~ ^(ssh-ed25519|ecdsa-sha2-nistp(256|384|521)|ssh-rsa)\ [A-Za-z0-9+/=]+(\ .*)?$ ]] \
    || { echo "--ssh-key must be one PUBLIC key line (ssh-ed25519 AAAA...)." >&2; exit 2; }
  [[ "$SSH_KEY" != *"PRIVATE KEY"* ]] || { echo "That is a private key. Ask for the .pub file instead." >&2; exit 2; }
fi

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO/deployment/contabo/.env"
OUT="/root/projectd-profiles/$NAME"
umask 077
mkdir -p "$OUT" && chmod 700 /root/projectd-profiles "$OUT"
SUMMARY=()

# --- VPN peer -------------------------------------------------------------------------------------------
if [[ -n "$WG_PUB" ]]; then
  if grep -qF "$WG_PUB" /etc/wireguard/wg0.conf; then
    SUMMARY+=("VPN: peer key already enrolled")
  else
    PEER_REGISTRY="/root/projectd-profiles/peers.md" \
      bash "$REPO/vpn/add-peer.sh" --name "$NAME" --ip "$WG_IP" --public-key "$WG_PUB" --apply >/dev/null
    SUMMARY+=("VPN: peer $WG_IP enrolled")
  fi
  server_pub="$(wg show wg0 public-key)"
  cat > "$OUT/wireguard-settings.txt" <<EOF
# WireGuard settings for $NAME. Contains no private key: safe to send through a normal private message.
# In the WireGuard app create an empty tunnel (it generates the key pair), keep its PrivateKey line,
# and set the rest as below.
[Interface]
Address = $WG_IP/32
MTU = 1420

[Peer]
PublicKey = $server_pub
Endpoint = ${ENDPOINT:-<server public IP>}:51820
AllowedIPs = 10.66.0.1/32
PersistentKeepalive = 25
EOF
  chmod 600 "$OUT/wireguard-settings.txt"
fi

# --- Linux account --------------------------------------------------------------------------------------
if [[ -n "$SSH_KEY" ]]; then
  if ! id "$NAME" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$NAME"
    # '*' = no usable password but not "locked", so key-only SSH works whatever the PAM settings.
    usermod -p '*' "$NAME"
  fi
  home="$(getent passwd "$NAME" | cut -d: -f6)"
  install -d -m 700 -o "$NAME" -g "$NAME" "$home/.ssh"
  touch "$home/.ssh/authorized_keys"
  grep -qxF "$SSH_KEY" "$home/.ssh/authorized_keys" || printf '%s\n' "$SSH_KEY" >> "$home/.ssh/authorized_keys"
  chown "$NAME:$NAME" "$home/.ssh/authorized_keys" && chmod 600 "$home/.ssh/authorized_keys"
  sudoers="/etc/sudoers.d/90-projectd-$NAME"
  if [[ "$SUDO" == 1 ]]; then
    printf '# Project D co-admin, added by add-member.sh\n%s ALL=(ALL) NOPASSWD:ALL\n' "$NAME" > "$sudoers.tmp"
    visudo -cqf "$sudoers.tmp" && mv "$sudoers.tmp" "$sudoers" && chmod 440 "$sudoers"
    SUMMARY+=("SSH: $NAME@server, key-only, WITH sudo (full root)")
  else
    rm -f "$sudoers"
    gpasswd -d "$NAME" docker >/dev/null 2>&1 || true
    SUMMARY+=("SSH: $NAME@server, key-only, no sudo, no docker group")
  fi
fi

# --- Grafana account ------------------------------------------------------------------------------------
[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE: run deploy.sh first." >&2; exit 1; }
grafana_result="$(python3 - "$ENV_FILE" "$NAME" "$ROLE" "$OUT/grafana-password.txt" <<'PY'
import base64, json, secrets, string, sys, urllib.error, urllib.request
from pathlib import Path

env_file, login, role, pw_file = sys.argv[1:5]
env = {}
for line in Path(env_file).read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
auth = base64.b64encode(f"{env.get('GRAFANA_ADMIN_USER', 'admin')}:{env['GRAFANA_ADMIN_PASSWORD']}".encode()).decode()

def call(method, path, body=None, ok_404=False):
    req = urllib.request.Request("http://127.0.0.1:13000" + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if ok_404 and e.code == 404:
            return None
        raise SystemExit(f"Grafana API {method} {path}: HTTP {e.code}")

user = call("GET", f"/api/users/lookup?loginOrEmail={login}", ok_404=True)
if user is None:
    alphabet = string.ascii_letters + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(24))
    created = call("POST", "/api/admin/users", {"name": login, "login": login, "password": password,
                                                "email": f"{login}@projectd.invalid"})
    Path(pw_file).write_text(f"Grafana login: {login}\nPassword: {password}\nChange it after the first login.\n",
                             encoding="utf-8")
    Path(pw_file).chmod(0o600)
    user_id, state = created["id"], "created"
else:
    user_id, state = user["id"], "existing (password unchanged)"
call("PATCH", f"/api/org/users/{user_id}", {"role": role})
print(f"Grafana: {login} {state}, role {role}")
PY
)"
SUMMARY+=("$grafana_result")

echo "Member $NAME:"
printf '  %s\n' "${SUMMARY[@]}"
echo "Hand-over files in $OUT (root only):"
[[ -f "$OUT/wireguard-settings.txt" ]] && echo "  wireguard-settings.txt  no secrets; a normal private message is fine"
[[ -f "$OUT/grafana-password.txt" ]] && echo "  grafana-password.txt    SECRET: send through a one-time link (1time.io), then: shred -u $OUT/grafana-password.txt"
exit 0
