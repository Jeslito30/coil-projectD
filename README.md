# Project D — two-city environmental monitoring

Project D collects simulated temperature, humidity and air-quality readings from Mati (Philippines) and Szczecin (Poland). Sensors publish over authenticated, TLS-encrypted MQTT through the WireGuard VPN. Telegraf validates the shared JSON contract, calculates end-to-end delay and writes to InfluxDB; Grafana provides the two-city dashboard and threshold alerts.

## Architecture

```text
Mati simulators ----\
                     |-- WireGuard --> Mosquitto --> Telegraf --> InfluxDB --> Grafana
Szczecin simulators -/   10.66.0.0/24   MQTT + TLS       bucket: sensors   dashboard + alerts
```

The baseline stack runs from `deployment/contabo/compose.yml`. MQTT and Grafana bind only to the server VPN address and loopback; InfluxDB remains on the private Docker network. The deployment uses a 30-day InfluxDB retention policy and provisions the Grafana data source, dashboard, alert rules and notification settings.

## Start here

1. Read the [data contract](documents/data-design.md), [architecture](documents/architecture.md) and [VPN plan](documents/vpn-plan.md).
2. Follow the [Contabo deployment runbook](deployment/contabo/README.md). Copy `.env.example` files, generate secrets locally and never commit real credentials.
3. Configure each city from [simulators/README.md](simulators/README.md). Run a simulator with `--dry-run` before connecting it to the broker.
4. Apply the VPN firewall rules and register peers as described in [vpn/peers.md](vpn/peers.md).
5. Configure [encrypted backups and scheduled recovery drills](backup/README.md).

## Offline checks

These checks require no MQTT broker and do not prove that the deployed infrastructure works:

```bash
python -m unittest discover -s simulators/tests -v
python simulators/projectd_simulator.py --city Szczecin --dry-run --count 1
python simulators/projectd_simulator.py --city Mati --dry-run --count 1
```

The GitHub Actions workflow additionally compiles Python sources, parses JSON/YAML, checks shell syntax and validates the Compose model using CI-only placeholder values.

## Repository map

| Path | Purpose |
| --- | --- |
| `simulators/` | Contract-compliant sensor simulators and offline tests |
| `docker/mosquitto/` | Broker TLS, account and ACL configuration |
| `deployment/contabo/` | Pinned Compose stack, Telegraf and server runbook |
| `grafana/` | Two-city dashboard, provisioning and alert rules |
| `vpn/` | WireGuard templates, peer registry and firewall rules |
| `tests/` | Delay analysis and infrastructure acceptance-test templates |
| `backup/` | Restic backup, systemd timer and guarded fresh-VM restore |
| `documents/` | Data design, architecture and implementation contracts |
| `evidence/` | Real test output and screenshots; templates are not evidence |

## Scope and optional Phase 7

The required baseline is MQTT → Telegraf → InfluxDB → Grafana. The custom FastAPI backend, Flutter application and Wake-on-LAN relay are optional Phase 7 work and are not needed to operate the baseline dashboard. See [backend/README.md](backend/README.md), [mobile/README.md](mobile/README.md) and the [WoL MQTT contract](documents/wol-contract.md). Draft contracts and placeholder directories must not be reported as completed implementations.

## Deployment status

Code, configuration templates and runbooks are prepared for review. A production-ready claim still requires real secrets, a Contabo VM, both cities connected through WireGuard, SMTP configuration and recorded acceptance tests for TLS/ACLs, delay, VPN interruption, alerting and fresh-VM restore.
