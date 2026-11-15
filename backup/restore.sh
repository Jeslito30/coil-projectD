#!/usr/bin/env bash
# Fresh-VM Project D restore. Dry-run by default; changes require --apply --yes.
set -Eeuo pipefail
umask 077

SNAPSHOT=latest
DEST=/opt/projectd-restored
APPLY=0
YES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --snapshot) SNAPSHOT="${2:?missing snapshot}"; shift 2 ;;
    --destination) DEST="${2:?missing destination}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --yes) YES=1; shift ;;
    -h|--help)
      echo "Usage: $0 [--snapshot latest|ID] [--destination PATH] [--apply --yes]"
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_ENV="${BACKUP_ENV:-$ROOT/backup/restic.env}"
[[ -r "$BACKUP_ENV" ]] || { echo "Missing Restic environment: $BACKUP_ENV" >&2; exit 1; }
set -a
source "$BACKUP_ENV"
set +a
: "${RESTIC_REPOSITORY:?set in restic.env}"
: "${RESTIC_PASSWORD:?set in restic.env}"
command -v restic >/dev/null || { echo "restic is required" >&2; exit 1; }

restic snapshots "$SNAPSHOT"
restic ls "$SNAPSHOT"
if [[ "$APPLY" != 1 ]]; then
  echo "Dry run only. Re-run with --apply --yes --destination PATH on a fresh isolated VM."
  exit 0
fi
[[ "$YES" == 1 ]] || { echo "--apply also requires --yes" >&2; exit 2; }
[[ "$DEST" == /* && "$DEST" != / && "$DEST" != /opt && "$DEST" != /srv ]] || {
  echo "Unsafe destination: $DEST" >&2; exit 2;
}
[[ ! -e "$DEST" ]] || { echo "Destination must not exist: $DEST" >&2; exit 1; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }

WORK="$(mktemp -d /var/tmp/projectd-restore.XXXXXX)"
trap 'rm -rf -- "$WORK"' EXIT
restic restore "$SNAPSHOT" --target "$WORK"
mapfile -t PROJECTS < <(find "$WORK" -type d -name project -print)
[[ "${#PROJECTS[@]}" == 1 ]] || { echo "Expected one backed-up project directory" >&2; exit 1; }
PROJECT_SRC="${PROJECTS[0]}"
SNAPSHOT_ROOT="$(dirname "$PROJECT_SRC")"
for path in influxdb grafana mosquitto-data; do
  [[ -d "$SNAPSHOT_ROOT/$path" ]] || { echo "Snapshot lacks $path" >&2; exit 1; }
done
[[ -r "$PROJECT_SRC/deployment/contabo/.env" ]] || { echo "Snapshot lacks deployment .env" >&2; exit 1; }

mkdir -p "$DEST"
cp -a "$PROJECT_SRC/." "$DEST/"
COMPOSE=(docker compose --env-file "$DEST/deployment/contabo/.env" -f "$DEST/deployment/contabo/compose.yml")
set -a
source "$DEST/deployment/contabo/.env"
set +a
: "${INFLUX_TOKEN:?missing in restored .env}"

# Start a fresh InfluxDB, wait for readiness, then restore its native full backup.
"${COMPOSE[@]}" up -d influxdb
for _ in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T influxdb influx ping >/dev/null 2>&1; then break; fi
  sleep 2
done
"${COMPOSE[@]}" exec -T influxdb influx ping
"${COMPOSE[@]}" exec -T influxdb rm -rf /tmp/projectd-influx-restore
"${COMPOSE[@]}" exec -T influxdb mkdir -p /tmp/projectd-influx-restore
"${COMPOSE[@]}" cp "$SNAPSHOT_ROOT/influxdb/." influxdb:/tmp/projectd-influx-restore/
"${COMPOSE[@]}" exec -T influxdb influx restore /tmp/projectd-influx-restore --full --token "$INFLUX_TOKEN"
"${COMPOSE[@]}" exec -T influxdb rm -rf /tmp/projectd-influx-restore

# Restore the stopped-service volumes before starting the complete stack.
"${COMPOSE[@]}" run --rm --no-deps --user root \
  -v "$SNAPSHOT_ROOT/grafana:/restore:ro" --entrypoint sh grafana \
  -c 'rm -rf /var/lib/grafana/* && cp -a /restore/. /var/lib/grafana/'
"${COMPOSE[@]}" run --rm --no-deps --user root \
  -v "$SNAPSHOT_ROOT/mosquitto-data:/restore:ro" --entrypoint sh mosquitto \
  -c 'rm -rf /mosquitto/data/* && cp -a /restore/. /mosquitto/data/'

"${COMPOSE[@]}" up -d
"${COMPOSE[@]}" ps

echo "Restore files and services are in $DEST"
echo "Now complete every acceptance check in tests/restore/restore-log.md, including historical data, new ingestion, Grafana alerts and VPN-only bindings."
