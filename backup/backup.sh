#!/usr/bin/env bash
# Consistent Project D backup to an encrypted, off-server Restic repository.
set -Eeuo pipefail
umask 077

ROOT="${PROJECT_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
BACKUP_ENV="${BACKUP_ENV:-$ROOT/backup/restic.env}"
[[ -r "$BACKUP_ENV" ]] || { echo "Missing Restic environment: $BACKUP_ENV" >&2; exit 1; }
[[ -r "$ROOT/deployment/contabo/.env" ]] || { echo "Missing deployment .env" >&2; exit 1; }

# These files are root-only configuration, not executable project code.
set -a
source "$BACKUP_ENV"
source "$ROOT/deployment/contabo/.env"
set +a
: "${RESTIC_REPOSITORY:?set in restic.env}"
: "${RESTIC_PASSWORD:?set in restic.env}"
: "${INFLUX_TOKEN:?set in deployment .env}"

command -v restic >/dev/null || { echo "restic is required" >&2; exit 1; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
COMPOSE=(docker compose --env-file "$ROOT/deployment/contabo/.env" -f "$ROOT/deployment/contabo/compose.yml")

STAGING_PARENT="${BACKUP_STAGING:-/var/tmp}"
mkdir -p "$STAGING_PARENT"
STAGING="$(mktemp -d "$STAGING_PARENT/projectd-backup.XXXXXX")"
CONTAINER_BACKUP=/tmp/projectd-influx-backup
STOPPED=0
cleanup() {
  rm -rf -- "$STAGING"
  "${COMPOSE[@]}" exec -T influxdb rm -rf "$CONTAINER_BACKUP" >/dev/null 2>&1 || true
  if [[ "$STOPPED" == 1 ]]; then
    "${COMPOSE[@]}" start grafana mosquitto >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

mkdir -p "$STAGING/influxdb" "$STAGING/grafana" "$STAGING/mosquitto-data" "$STAGING/project"
echo "[$(date -u +%FT%TZ)] Creating consistent service backups"

# InfluxDB's own backup format is used instead of copying a live database volume.
"${COMPOSE[@]}" exec -T influxdb rm -rf "$CONTAINER_BACKUP"
"${COMPOSE[@]}" exec -T influxdb influx backup "$CONTAINER_BACKUP" --token "$INFLUX_TOKEN"
"${COMPOSE[@]}" cp "influxdb:$CONTAINER_BACKUP/." "$STAGING/influxdb/"
"${COMPOSE[@]}" exec -T influxdb rm -rf "$CONTAINER_BACKUP"

# Briefly stop Grafana and Mosquitto for consistent SQLite and persistence copies.
# Telegraf/simulators reconnect automatically; record this maintenance window in the log.
"${COMPOSE[@]}" stop grafana mosquitto
STOPPED=1
"${COMPOSE[@]}" cp grafana:/var/lib/grafana/. "$STAGING/grafana/"
"${COMPOSE[@]}" cp mosquitto:/mosquitto/data/. "$STAGING/mosquitto-data/"
"${COMPOSE[@]}" start grafana mosquitto
STOPPED=0

# Restic encryption protects the configuration and secrets needed for a fresh-VM restore.
for path in backup deployment docker documents grafana simulators tests vpn; do
  [[ ! -e "$ROOT/$path" ]] || cp -a "$ROOT/$path" "$STAGING/project/"
done

# The WireGuard server key is part of the service identity: without it a restored server gets a new
# public key and every peer profile must be re-issued. It stays inside the encrypted snapshot only.
WG_CONFIG_DIR="${WG_CONFIG_DIR:-/etc/wireguard}"
if [[ -r "$WG_CONFIG_DIR/wg0.conf" ]]; then
  mkdir -p "$STAGING/wireguard"
  cp -a "$WG_CONFIG_DIR/." "$STAGING/wireguard/"
else
  echo "WARNING: $WG_CONFIG_DIR/wg0.conf not readable; the snapshot will not contain the WireGuard server key." >&2
fi
{
  echo "created_utc=$(date -u +%FT%TZ)"
  if command -v git >/dev/null && git -C "$ROOT" rev-parse HEAD >/dev/null 2>&1; then
    echo "git_commit=$(git -C "$ROOT" rev-parse HEAD)"
  fi
  "${COMPOSE[@]}" images
} > "$STAGING/project-backup-metadata.txt" 2>/dev/null || true

echo "[$(date -u +%FT%TZ)] Uploading encrypted snapshot"
restic backup "$STAGING" --tag project-d --tag automated
# With an append-only repository (rest-server --append-only, or a cloud key without delete rights) the
# server cannot delete snapshots, so a compromised host cannot destroy its own backups. Set
# RESTIC_APPEND_ONLY=1 in restic.env and run the retention below from a separate trusted machine.
if [[ "${RESTIC_APPEND_ONLY:-0}" == "1" ]]; then
  echo "RESTIC_APPEND_ONLY=1: skipping forget/prune on this host; run retention from a trusted machine."
else
  restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 3 --prune
fi
restic snapshots --latest 1

echo "[$(date -u +%FT%TZ)] Backup complete; snapshot verified by a successful upload"
