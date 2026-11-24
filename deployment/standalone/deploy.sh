#!/usr/bin/env bash
# Project D - standalone deployment on a fresh Ubuntu 24.04 server, from this repository only.
#
# Builds an independent copy of the whole stack with NEW secrets (no restic/B2 backup needed):
#   WireGuard wg0 10.66.0.1/24 (new server key) + one laptop peer profile,
#   MQTT CA/server certificate and per-sensor accounts, .env with random secrets,
#   Mosquitto + Telegraf + InfluxDB + Grafana + Team Dashboard (deployment/contabo/compose.yml),
#   least-privilege InfluxDB tokens, both city simulators (Mati + Szczecin) as systemd services,
#   and an end-to-end check that all six sensors reach InfluxDB. Every phase is timed.
#
# It does NOT touch the production server and does NOT reuse its keys, so existing VPN/QR profiles
# keep pointing at production. Historical data can be restored later from B2 (backup/README.md).
#
# Usage (as root, from anywhere):
#   bash /opt/coil-projectD/deployment/standalone/deploy.sh --endpoint <public-ip-or-dns> [--peer-name darek-laptop] [--peer-ip 10.66.0.22]
# Re-running is safe: existing keys, certificates, passwords and .env are kept, never regenerated.
set -Eeuo pipefail

ENDPOINT="" PEER_NAME="darek-laptop" PEER_IP="10.66.0.22"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --endpoint) ENDPOINT="${2:-}"; shift 2 ;;
    --peer-name) PEER_NAME="${2:-}"; shift 2 ;;
    --peer-ip) PEER_IP="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$ENDPOINT" ]] || { echo "--endpoint <public-ip-or-dns> is required" >&2; exit 2; }
[[ "$(id -u)" == 0 ]] || { echo "Run as root." >&2; exit 1; }

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO/deployment/contabo/.env"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$REPO/deployment/contabo/compose.yml")
MQ="$REPO/docker/mosquitto"
SECRETS_DIR="/root/projectd-secrets"
SIM_ETC="/etc/projectd/sim"
SIM_VAR="/var/lib/projectd-sim"
SIM_VENV="/opt/projectd-sim/venv"
EVIDENCE="/root/projectd-evidence"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="$EVIDENCE/standalone-deploy-$STAMP.txt"
mkdir -p "$EVIDENCE" && chmod 700 "$EVIDENCE"

T0=$(date +%s); PHASE_START=$T0
log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" | tee -a "$REPORT"; }
phase_done() {
  local now; now=$(date +%s)
  printf '  -> %-28s %4ss (total %ss)\n' "$1" "$((now - PHASE_START))" "$((now - T0))" | tee -a "$REPORT"
  PHASE_START=$now
}
fail() { log "FAILED: $*"; log "Report: $REPORT"; exit 1; }
trap 'fail "line $LINENO: $BASH_COMMAND"' ERR

log "Project D standalone deployment, repo $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown), endpoint $ENDPOINT"

# --- 1. Packages --------------------------------------------------------------------------------------
command -v docker >/dev/null && docker compose version >/dev/null 2>&1 || fail "Docker Engine with the Compose plugin is required"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git wireguard-tools qrencode openssl python3-venv curl mosquitto >/dev/null
# Only mosquitto_passwd is needed from the host package; the broker runs in Docker.
systemctl disable --now mosquitto >/dev/null 2>&1 || true
phase_done "packages"

# --- 2. WireGuard ---------------------------------------------------------------------------------------
if [[ ! -f /etc/wireguard/wg0.conf ]]; then
  umask 077
  key="$(wg genkey)"
  sed "s|<SERVER_PRIVATE_KEY>|$key|" "$REPO/vpn/server/wg0.conf.template" > /etc/wireguard/wg0.conf
  unset key
  chmod 600 /etc/wireguard/wg0.conf
  log "Created /etc/wireguard/wg0.conf with a new server key"
else
  log "Keeping existing /etc/wireguard/wg0.conf"
fi
sysctl -q -w net.ipv4.ip_forward=1
systemctl enable --now wg-quick@wg0 >/dev/null
ip -4 addr show wg0 | grep -q '10.66.0.1/24' || fail "wg0 does not carry 10.66.0.1/24"
# Containers bind to 10.66.0.1, so Docker must start after wg0 on every boot.
install -D -m 644 "$REPO/deployment/contabo/docker-after-wireguard.conf" /etc/systemd/system/docker.service.d/after-wireguard.conf
systemctl daemon-reload
if command -v ufw >/dev/null && ufw status | grep -q 'Status: active'; then
  ufw status | grep -q '51820/udp' || ufw allow 51820/udp comment 'WireGuard' >/dev/null
fi
phase_done "wireguard"

# --- 3. MQTT certificates and accounts ------------------------------------------------------------------
mkdir -p "$SECRETS_DIR" && chmod 700 "$SECRETS_DIR"
if [[ ! -f "$MQ/certs/server.key" ]]; then
  bash "$MQ/scripts/gen-certs.sh" "$MQ/certs" >/dev/null
  log "Created the MQTT CA and server certificate"
fi
# The CA private key is only needed to issue certificates: keep it outside the repository tree.
if [[ -f "$MQ/certs/ca.key" ]]; then
  mv "$MQ/certs/ca.key" "$SECRETS_DIR/ca.key" && chmod 600 "$SECRETS_DIR/ca.key"
fi
if [[ ! -f "$MQ/config/passwd" ]]; then
  bash "$MQ/scripts/gen-passwd.sh" "$MQ/config" "$SECRETS_DIR/mqtt-passwords.env" >/dev/null
  log "Created MQTT accounts (passwords in $SECRETS_DIR/mqtt-passwords.env)"
fi
[[ -f "$SECRETS_DIR/mqtt-passwords.env" ]] || fail "$SECRETS_DIR/mqtt-passwords.env is missing but config/passwd exists"
# The broker container runs as uid 1883 and must read its key and password file.
chown root:1883 "$MQ/certs/server.key" "$MQ/config/passwd"
chmod 640 "$MQ/certs/server.key" "$MQ/config/passwd"
phase_done "mqtt certificates/accounts"

# --- 4. .env with random secrets ------------------------------------------------------------------------
rand() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | cut -c1-"$1"; }
if [[ ! -f "$ENV_FILE" ]]; then
  umask 077
  telegraf_pw="$(grep '^MQTT_TELEGRAF_PASSWORD=' "$SECRETS_DIR/mqtt-passwords.env" | cut -d= -f2-)"
  [[ -n "$telegraf_pw" ]] || fail "telegraf password not found in mqtt-passwords.env"
  cat > "$ENV_FILE" <<EOF
# Generated by deployment/standalone/deploy.sh on $STAMP. Never commit or paste this file.
INFLUX_ADMIN_USER=projectd-admin
INFLUX_ADMIN_PASSWORD=$(rand 32)
INFLUX_ORG=coil
INFLUX_TOKEN=$(rand 64)
INFLUX_TELEGRAF_TOKEN=
INFLUX_GRAFANA_TOKEN=
MQTT_TELEGRAF_PASSWORD=$telegraf_pw
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=$(rand 24)
GRAFANA_ALERT_EMAIL=alerts@example.org
GF_SMTP_ENABLED=false
GF_SMTP_HOST=
GF_SMTP_FROM_ADDRESS=
GF_SMTP_USER=
GF_SMTP_PASSWORD=
MIRROR_B2_BUCKET=
MIRROR_B2_KEY_ID=
MIRROR_B2_APP_KEY=
EOF
  unset telegraf_pw
  chmod 600 "$ENV_FILE"
  log "Created $ENV_FILE with random secrets"
else
  log "Keeping existing $ENV_FILE"
fi
phase_done "secrets (.env)"

# --- 5. Start the stack ---------------------------------------------------------------------------------
"${COMPOSE[@]}" config -q
"${COMPOSE[@]}" pull -q
"${COMPOSE[@]}" up -d
for _ in $(seq 1 60); do
  healthy=$("${COMPOSE[@]}" ps --format '{{.Service}} {{.Health}}' | grep -cE '^(influxdb|grafana|team-dashboard) healthy$' || true)
  [[ "$healthy" == 3 ]] && break
  sleep 5
done
[[ "${healthy:-0}" == 3 ]] || { "${COMPOSE[@]}" ps | tee -a "$REPORT"; fail "influxdb/grafana/team-dashboard not healthy after 5 min"; }
phase_done "stack up (healthy)"

# --- 6. Least-privilege InfluxDB tokens -----------------------------------------------------------------
if ! grep -q '^INFLUX_TELEGRAF_TOKEN=.\+' "$ENV_FILE"; then
  python3 "$REPO/deployment/contabo/scoped_tokens.py" --env-file "$ENV_FILE" >/dev/null
  "${COMPOSE[@]}" up -d --no-deps telegraf grafana team-dashboard >/dev/null
  log "Telegraf/Grafana/Team Dashboard now use scoped tokens"
fi
phase_done "scoped tokens"

# --- 7. Simulators (both cities on this server, through the broker's VPN address) -----------------------
id projectd-sim >/dev/null 2>&1 || useradd --system --home-dir "$SIM_VAR" --shell /usr/sbin/nologin projectd-sim
mkdir -p "$SIM_ETC" "$SIM_VAR" "$(dirname "$SIM_VENV")"
[[ -x "$SIM_VENV/bin/python" ]] || python3 -m venv "$SIM_VENV"
"$SIM_VENV/bin/pip" install -q -r "$REPO/simulators/requirements.txt"
install -m 644 "$MQ/certs/ca.crt" "$SIM_ETC/ca.crt"
for city in mati szczecin; do
  case "$city" in mati) prefix=MQTT_MATI_ ;; szczecin) prefix=MQTT_SZCZ_ ;; esac
  mkdir -p "$SIM_VAR/$city"
  chown projectd-sim:projectd-sim "$SIM_VAR/$city"
  # Each city gets only its own three sensor passwords.
  grep "^$prefix" "$SECRETS_DIR/mqtt-passwords.env" > "$SIM_ETC/$city.env"
  [[ "$(wc -l < "$SIM_ETC/$city.env")" == 3 ]] || fail "expected 3 passwords for $city"
  chown root:projectd-sim "$SIM_ETC/$city.env" && chmod 640 "$SIM_ETC/$city.env"
  sed -e "s|^\(\s*ca_file:\).*|\1 $SIM_ETC/ca.crt|" \
      -e "s|^log_file:.*|log_file: $SIM_VAR/$city/simulator-$city.jsonl|" \
      "$REPO/simulators/$city/config.example.yaml" > "$SIM_ETC/$city.yaml"
  chmod 644 "$SIM_ETC/$city.yaml"
done
cat > /etc/systemd/system/projectd-sim@.service <<EOF
[Unit]
Description=Project D sensor simulator (%i)
After=docker.service wg-quick@wg0.service
Wants=wg-quick@wg0.service

[Service]
User=projectd-sim
Group=projectd-sim
WorkingDirectory=$SIM_VAR/%i
ExecStart=$SIM_VENV/bin/python $REPO/simulators/projectd_simulator.py --config $SIM_ETC/%i.yaml --secrets-file $SIM_ETC/%i.env --quiet
Restart=always
RestartSec=10
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$SIM_VAR/%i
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now projectd-sim@mati projectd-sim@szczecin >/dev/null
phase_done "simulators"

# --- 8. Laptop VPN profile ------------------------------------------------------------------------------
PROFILE="/root/projectd-profiles/$PEER_NAME/$PEER_NAME.conf"
if [[ ! -f "$PROFILE" ]]; then
  PEER_REGISTRY="/root/projectd-profiles/peers.md" \
    bash "$REPO/vpn/make-phone-profile.sh" --name "$PEER_NAME" --ip "$PEER_IP" --endpoint "$ENDPOINT:51820" --apply >/dev/null 2>&1 \
    || fail "could not create the $PEER_NAME profile"
  log "Created VPN profile $PROFILE"
fi
phase_done "vpn profile"

# --- 9. End-to-end check: six sensors in InfluxDB -------------------------------------------------------
log "Waiting up to 3 min for readings from all six sensors..."
token="$(grep '^INFLUX_TOKEN=' "$ENV_FILE" | cut -d= -f2-)"
sensors=0
for _ in $(seq 1 18); do
  sleep 10
  sensors=$("${COMPOSE[@]}" exec -T influxdb influx query --org coil --token "$token" --raw \
    'from(bucket:"sensors") |> range(start:-5m) |> filter(fn:(r) => r._measurement == "environment" and r._field == "value") |> group(columns:["sensor_id"]) |> count()' \
    2>/dev/null | grep -oE '(MATI|SZCZ)-[A-Z]+-[0-9]+' | sort -u | wc -l || true)
  [[ "$sensors" -ge 6 ]] && break
done
unset token
log "Sensors with readings in the last 5 min: $sensors/6"
[[ "$sensors" -ge 6 ]] || { journalctl -u 'projectd-sim@*' -n 20 --no-pager | tee -a "$REPORT"; fail "not all sensors reached InfluxDB"; }
curl -fsS http://127.0.0.1:13000/api/health >/dev/null || fail "Grafana health check"
curl -fsS http://127.0.0.1:18088/healthz >/dev/null || fail "Team Dashboard health check"
phase_done "end-to-end check"

# --- 10. Exposure check and summary ---------------------------------------------------------------------
public=$(ss -lntuH | awk '{print $5}' | grep -vE '^(127\.|\[::1\]|10\.66\.0\.1:|0\.0\.0\.0:22$|\[::\]:22$|\*:22$|0\.0\.0\.0:51820$|\[::\]:51820$|\*:51820$)' | sort -u || true)
if [[ -n "$public" ]]; then log "WARNING: unexpected listeners: $(echo "$public" | tr '\n' ' ')"; else log "Public listeners: SSH 22/tcp and WireGuard 51820/udp only"; fi
trap - ERR
TOTAL=$(( $(date +%s) - T0 ))
log "DEPLOY_STATUS=0 total ${TOTAL}s ($(date -u -d "@$TOTAL" +%H:%M:%S))"
cat <<EOF | tee -a "$REPORT"

Next steps
  1. Copy the laptop profile to Windows (PowerShell):  scp root@$ENDPOINT:$PROFILE .
     Import it in the WireGuard app. Only one tunnel to 10.66.0.0/24 can be active at a time.
  2. With the tunnel on: Grafana http://10.66.0.1:3000 (user admin), Team Dashboard http://10.66.0.1:8088
     Grafana password, on the server:  grep ^GRAFANA_ADMIN_PASSWORD= $ENV_FILE
  3. Secrets stay on this server: $ENV_FILE, $SECRETS_DIR/ (CA key, MQTT passwords). Never post them.
Report: $REPORT
EOF
