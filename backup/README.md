# Encrypted backup and fresh-VM restore

The backup job creates a native InfluxDB backup, briefly stops Grafana and Mosquitto for consistent file copies, and sends the service data plus the deployment configuration to an encrypted Restic repository. Restic keeps 7 daily, 4 weekly and 3 monthly snapshots.

**A successful upload is not a restore test.** The recovery drill in `tests/restore/restore-log.md` is still required. Keep the Restic password and a copy of this repository outside the Contabo VM; without them the remote snapshots cannot be recovered.

## 1. Configure the repository

Install Docker, the Compose plugin and Restic. On the server, as root:

```bash
install -d -m 700 /etc/projectd
install -m 600 backup/restic.env.example /etc/projectd/restic.env
chmod 600 /etc/projectd/restic.env
$EDITOR /etc/projectd/restic.env
```

Set `RESTIC_REPOSITORY` to an off-server location and generate a unique `RESTIC_PASSWORD` locally. Add only the credentials required by the selected backend. Never commit the completed file.

Initialize the repository once and verify access:

```bash
set -a
source /etc/projectd/restic.env
set +a
restic init          # only for a new, empty repository
restic snapshots
```

## 2. Run and schedule backups

The repository is expected at `/opt/coil-projectD` by the supplied systemd units. Run the first backup manually before enabling the timer:

```bash
chmod 750 backup/backup.sh backup/restore.sh
PROJECT_ROOT=/opt/coil-projectD BACKUP_ENV=/etc/projectd/restic.env \
  /opt/coil-projectD/backup/backup.sh

install -m 644 backup/projectd-backup.service backup/projectd-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now projectd-backup.timer
systemctl list-timers projectd-backup.timer
journalctl -u projectd-backup.service -n 100 --no-pager
```

The timer runs nightly at 02:30 local server time with a random delay of up to 15 minutes. `Persistent=true` catches up after downtime. Grafana and Mosquitto are briefly unavailable during each file-consistent copy; clients must reconnect automatically.

Useful manual checks:

```bash
systemctl start projectd-backup.service
restic snapshots
restic check
```

## 3. Restore onto a fresh isolated VM

Install Docker, Compose and Restic, clone the project, and place the recovery credentials at `/etc/projectd/restic.env`. Start with the default dry run; it only lists the snapshot:

```bash
BACKUP_ENV=/etc/projectd/restic.env backup/restore.sh --snapshot latest
```

Inspect the listed files. Then choose a new absolute destination and explicitly apply the restore:

```bash
BACKUP_ENV=/etc/projectd/restic.env backup/restore.sh \
  --snapshot latest --destination /opt/projectd-restored --apply --yes
```

The script restores InfluxDB through its native backup format, restores Grafana and Mosquitto volumes while those services are stopped, and starts the Compose stack. It refuses an existing destination and never overwrites the original installation.

Finally complete every check in `tests/restore/restore-log.md`: historical samples, new end-to-end ingestion, dashboard and alert recovery, authentication/ACLs, and VPN-only port bindings. Record the actual date, snapshot ID, timings and evidence. Do not mark recovery tested from a dry run or from these commands alone.

## One-time export from the existing `coil-checkin2` stack (migration)

The data moves to the dedicated VPS through a normal backup and the fresh-VM restore, so the migration doubles as the required restore test. On the new server the data lands in the repository's own volumes (`coil-projectd_*`). Do not rename volumes in `compose.yml` to the old names.

On Contabo, in a reviewed checkout of the repository:

1. Copy the live broker files into the checkout, so sensors keep their credentials and CA on the new server: `passwd`, `acl` → `docker/mosquitto/config/`, `ca.crt`, `server.crt`, `server.key` → `docker/mosquitto/certs/` (not `ca.key`).
2. Create `deployment/contabo/.env` (root, mode 600) with the **current** values: `INFLUX_TOKEN` = the existing operator token, the InfluxDB and Grafana admin credentials, `MQTT_TELEGRAF_PASSWORD`, SMTP settings. The restored server uses this file as is.
3. Record the source inventory for a fixed window: `COMPOSE_PROJECT_NAME=coil-checkin2 python3 tests/restore/inventory.py snapshot --stop <time> > inventory-source.csv`
4. Back up the running stack: `sudo COMPOSE_PROJECT_NAME=coil-checkin2 backup/backup.sh`. The service names match, so `exec`/`cp`/`stop`/`start` act on the running containers. Grafana and Mosquitto stop for a few seconds; sensors queue and resend.

> **Never** run `docker compose up`, `down` or `run` with `COMPOSE_PROJECT_NAME=coil-checkin2`. The running InfluxDB volume is `coil-checkin2_influx-data`, but the repository's key is `influxdb-data`, so `up` would recreate InfluxDB on an empty volume.

On the new VPS: `backup/restore.sh --snapshot latest --apply --yes`, then `inventory.py snapshot` with the same window, then `inventory.py compare`. Keep `coil-checkin2` running until the comparison passes; it is the rollback.

