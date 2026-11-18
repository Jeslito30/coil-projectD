# Contabo deployment — Project D

This compose stack runs Mosquitto (MQTT TLS), Telegraf, InfluxDB 2.x and Grafana. The team dashboard is the provisioned Grafana dashboard **Project D - Mati + Szczecin**. No custom backend is needed for the mandatory monitoring baseline.

## Network and data policy

- MQTT: `10.66.0.1:8883` over WireGuard; local administration also uses `127.0.0.1:18883`.
- Grafana: `http://10.66.0.1:3000` over WireGuard; local administration also uses `http://127.0.0.1:13000`.
- InfluxDB has **no host port** and is reachable only by containers on the `projectd` network.
- InfluxDB organisation: `coil`; bucket: `sensors`; retention: **30 days** (`720h`). Alert 2 and the latest-readings table use the same 30-day lookback.
- The host also runs unrelated services. Do not apply a broad default-deny UFW script. Review and apply only `vpn/firewall/docker-user.sh` after peers are enrolled.

## 0. Adopting an existing deployment

A Project D stack is already running on Contabo. **Do not run `docker compose up` from this repository on that host until this section is complete.** Compose names volumes after the project (`coil-projectd_influxdb-data`, ...). If the running stack uses another project name or anonymous volumes, `up` starts a second, empty stack instead of reusing the data, and the two stacks conflict on ports.

1. Run the read-only check and keep the report with the evidence (it prints no secrets):

   ```bash
   sudo python3 deployment/contabo/reconcile_check.py --env-file deployment/contabo/.env > reconcile-$(date -u +%F).md
   ```

   It lists, per service, the running project, image, data mount and published ports against `compose.yml`, plus `10.66.0.1`, `wg0` and the first `DOCKER-USER` rule. Exit status 1 means at least one **HIGH** finding.

2. Resolve every HIGH finding before switching. For data on another volume name, take a verified backup (`backup/README.md`), stop the old stack, and copy each data volume into the name the repository expects, for example:

   ```bash
   docker volume create coil-projectd_influxdb-data
   docker run --rm --entrypoint sh -v <old-influx-volume>:/from:ro -v coil-projectd_influxdb-data:/to \
     influxdb:2.7.12 -c 'cp -a /from/. /to/'
   ```

   Repeat for Grafana (`/var/lib/grafana`) and Mosquitto (`/mosquitto/data`). Keep the old volumes until the acceptance checks in section 5 pass on the new stack; that is the rollback.

3. Re-run the check. Only an empty findings list (or reviewed INFO/MEDIUM items) clears the switch.

## 1. Prepare the host

Install Docker Engine with the Compose plugin, WireGuard and an NTP client. Configure `wg0` as `10.66.0.1/24`, enrol only the public keys in `vpn/peers.md`, and verify clock synchronisation before measuring delay.

Clone the repository and run all commands below from its root. Review the pinned images and configuration before starting anything.

## 2. Generate MQTT certificates and accounts

```bash
docker/mosquitto/scripts/gen-certs.sh
docker/mosquitto/scripts/gen-passwd.sh
chmod 600 docker/mosquitto/secrets/mqtt-passwords.env
```

The certificate SANs must include `IP:10.66.0.1` and `DNS:mosquitto`. Move `docker/mosquitto/certs/ca.key` off the server into approved secret storage. Give each simulator only its public `ca.crt` and its own password through a private channel. Never commit `ca.key`, `server.key`, `config/passwd`, `.env` or the generated password file.

## 3. Configure secrets and start

```bash
cp deployment/contabo/.env.example deployment/contabo/.env
chmod 600 deployment/contabo/.env
$EDITOR deployment/contabo/.env

docker compose --env-file deployment/contabo/.env \
  -f deployment/contabo/compose.yml config -q
docker compose --env-file deployment/contabo/.env \
  -f deployment/contabo/compose.yml up -d
docker compose --env-file deployment/contabo/.env \
  -f deployment/contabo/compose.yml ps
```

Generate strong, independent values for the InfluxDB administrator password/token and Grafana administrator password. `MQTT_TELEGRAF_PASSWORD` must match the generated `telegraf` MQTT account. InfluxDB setup variables initialise a new volume only; changing them later does not rotate existing credentials.

## 4. Start both simulators

Follow `simulators/README.md`. Use the Mati and Szczecin configuration examples, one process per city, and keep the nominal 30-second interval for the demonstration. Each city must connect through its own WireGuard profile to `10.66.0.1:8883`.

## 5. Validate the stack

```bash
# No public project ports other than WireGuard should appear.
sudo ss -lntup

# Confirm MQTT TLS and ACLs using the existing security test.
python tests/security/mqtt_security_test.py --help

# Check ingestion logs without printing secrets.
docker compose -f deployment/contabo/compose.yml logs --since=10m telegraf
```

In Grafana, open **Project D - Mati + Szczecin** and confirm six sensors, shared charts, latest values and ages. Use `--anomaly <sensor_id>` and `--silence <sensor_id>` to verify both alert rules and recovery.

### Email delivery

Email is a separate acceptance test. Set `GF_SMTP_ENABLED=true` and the `GF_SMTP_*` values in `.env`, recreate Grafana (`up -d grafana`), then test in two steps so a failure points at one layer:

```bash
python3 grafana/smtp_test.py direct  --env-file deployment/contabo/.env   # SMTP host, TLS, login, sender
python3 grafana/smtp_test.py grafana --env-file deployment/contabo/.env   # Grafana's own SMTP settings
```

Then trigger Alert 1 and Alert 2 and keep the received emails (with headers) as evidence. Do not claim email delivery merely because an alert fired or a test message was sent.

### Faculty Viewer accounts

Keep the list outside Git (format: `grafana/viewers.csv.example`) and create or repair the accounts over loopback:

```bash
sudo install -m 600 grafana/viewers.csv.example /etc/projectd/viewers.csv && sudoedit /etc/projectd/viewers.csv
python3 grafana/viewer_accounts.py --users /etc/projectd/viewers.csv --env-file deployment/contabo/.env --dry-run
python3 grafana/viewer_accounts.py --users /etc/projectd/viewers.csv --env-file deployment/contabo/.env
```

Re-running is safe: existing users keep their password and their role is forced back to **Viewer**. New passwords go only to `grafana-viewer-passwords.txt` (mode 600); deliver them privately, ask for a change at first login, then delete the file. Viewers also need their VPN profile (`.31`, `.32`). Do not share the administrator account and do not enable anonymous access.

## 6. Firewall and persistence

After positive connectivity tests, review and apply the active-peer rules:

```bash
sudo vpn/firewall/docker-user.sh
sudo systemctl enable --now project-d-docker-user.service
sudo vpn/firewall/docker-user.sh --check     # after every reboot and Docker upgrade
```

Install the supplied systemd unit first if it is not already present. Verify allowed and denied sources, then repeat after reboot. The firewall currently allows only active member peers; reserved addresses require enrolment and a reviewed rule update. `--remove` rolls the rules back.

The rules live in a dedicated `PROJECTD-WG` chain reached from the first `DOCKER-USER` rule. The previous version appended its DROP to `DOCKER-USER`, where a trailing `-j RETURN` (present on many Docker versions) made it ineffective; re-running the current script removes those old rules. `sudo tests/security/firewall-netns-test.sh` checks the script offline with real packets.

The rules only filter traffic arriving on `wg0`. Unrelated services on this host (Coolify, Supabase) are not reachable from VPN peers through Docker-published ports while the rules are active.

## 7. Backup and restore

Configure the encrypted, off-server Restic repository described in `backup/README.md`. Run and check a backup, enable the nightly timer, and perform the fresh-VM exercise in `tests/restore/restore-log.md`. `tests/restore/inventory.py` produces the per-sensor data comparison for that log. Repository files alone are not evidence of a successful backup or restore.

## 8. Updates and rollback

Review pull requests before deployment. Record image digests and the project commit in the evidence log. To update, pull the reviewed commit, run `config -q`, then `up -d`; to roll back, return to the previous commit and known-good image digests. Do not delete named volumes during an update or rollback.
