# Contabo deployment — Project D

This compose stack runs Mosquitto (MQTT TLS), Telegraf, InfluxDB 2.x and Grafana. The team dashboard is the provisioned Grafana dashboard **Project D - Mati + Szczecin**. No custom backend is needed for the mandatory monitoring baseline.

## Network and data policy

- MQTT: `10.66.0.1:8883` over WireGuard; local administration also uses `127.0.0.1:18883`.
- Grafana: `http://10.66.0.1:3000` over WireGuard; local administration also uses `http://127.0.0.1:13000`.
- InfluxDB has **no host port** and is reachable only by containers on the `projectd` network.
- InfluxDB organisation: `coil`; bucket: `sensors`; retention: **30 days** (`720h`). Alert 2 and the latest-readings table use the same 30-day lookback.
- The host also runs unrelated services. Do not apply a broad default-deny UFW script. Review and apply only `vpn/firewall/docker-user.sh` after peers are enrolled.

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

In Grafana, open **Project D - Mati + Szczecin** and confirm six sensors, shared charts, latest values and ages. Use `--anomaly <sensor_id>` and `--silence <sensor_id>` to verify both alert rules and recovery. Email delivery is a separate acceptance test: set `GF_SMTP_ENABLED=true`, supply SMTP settings, restart Grafana and use **Contact points → Test**. Do not claim email delivery merely because an alert fired.

Create named faculty **Viewer** accounts through Grafana Administration. Do not share the administrator account and do not enable anonymous access.

## 6. Firewall and persistence

After positive connectivity tests, review and apply the active-peer rules:

```bash
sudo vpn/firewall/docker-user.sh
sudo systemctl enable --now project-d-docker-user.service
```

Install the supplied systemd unit first if it is not already present. Verify allowed and denied sources, then repeat after reboot. The firewall currently allows only active member peers; reserved addresses require enrolment and a reviewed rule update.

## 7. Backup and restore

Configure the encrypted, off-server Restic repository described in `backup/README.md`. Run and check a backup, enable the nightly timer, and perform the fresh-VM exercise in `tests/restore/restore-log.md`. Repository files alone are not evidence of a successful backup or restore.

## 8. Updates and rollback

Review pull requests before deployment. Record image digests and the project commit in the evidence log. To update, pull the reviewed commit, run `config -q`, then `up -d`; to roll back, return to the previous commit and known-good image digests. Do not delete named volumes during an update or rollback.
