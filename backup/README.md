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

Set `RESTIC_REPOSITORY` to an off-server location and generate a unique `RESTIC_PASSWORD` locally.

Prefer a repository the server can only **append** to (`rest-server --append-only`, or a cloud key without delete rights) and set `RESTIC_APPEND_ONLY=1`. Root on the server can otherwise run `restic forget` and delete every snapshot. That matters while the host also runs other internet-facing services. Retention (`forget --prune`) then runs from a separate trusted machine. Add only the credentials required by the selected backend. Never commit the completed file.

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

## Backing up the existing `coil-checkin2` stack (Check-in 4) and migrating it

The running stack is backed up through **its own** compose and `.env` files. Nothing is changed or restarted except the short Grafana/Mosquitto stop, which `BACKUP_STOP_SERVICES=0` avoids. Later the same snapshots feed the fresh-VM restore on the dedicated VPS (Check-in 5), where the data lands in the repository's own volumes (`coil-projectd_*`). Do not rename volumes in `compose.yml` to the old names.

1. `sudo install -m 600 backup/backup.env.example /etc/projectd/backup.env` and set the paths: the live compose file and `.env`, the name of the token variable, and `BACKUP_EXTRA_PATHS` = the live deployment directory, so its compose file, `.env`, Telegraf and Mosquitto configuration (passwd, ACL, certificates) and Grafana provisioning are in the encrypted snapshot.
2. Configure `/etc/projectd/restic.env` (`restic.env.example`) and `restic init`.
3. First run, at a quiet time and watched: `sudo systemctl start projectd-backup.service`, then `journalctl -u projectd-backup -n 50` and `restic snapshots`. Confirm Grafana and Mosquitto are back (`docker ps`) and readings continue.
4. Enable the timer: `sudo systemctl enable --now projectd-backup.timer`.

For the migration: record `tests/restore/inventory.py snapshot --stop <time> --compose-file <live compose> --env-file <live .env>` on the source, then on the new VPS run `backup/restore.sh --snapshot latest --apply --yes` and the same inventory, then `inventory.py compare`. The live configuration is under `extra/` in the restored snapshot. Copy `passwd`, `acl` and the certificates from there into the checkout (never `ca.key`), so sensors keep their credentials. Keep `coil-checkin2` running until the comparison passes; it is the rollback.

> **Never** run `docker compose up`, `down` or `run` from this repository with `COMPOSE_PROJECT_NAME=coil-checkin2`. The running InfluxDB volume is `coil-checkin2_influx-data`, but the repository's key is `influxdb-data`, so `up` would recreate InfluxDB on an empty volume.
