# Project D Documentation — Two-City IoT Sensor Dashboard

**Version:** 0.2 · **Status:** For joint PH/PL review · **Updated:** 2026-11-04

This directory is the design baseline for Project D. It defines how simulated sensors in Mati and Szczecin publish data through WireGuard and MQTT over TLS to a central cloud VM, where Telegraf, InfluxDB and Grafana provide storage, visualisation and alerts. It also covers backup, recovery and the optional Wake-on-LAN extension.

## Document map

| Document | Purpose | Lead | Review state |
| --- | --- | --- | --- |
| [architecture.md](architecture.md) | Components, data flow, trust boundaries, failure behaviour, backup and optional WoL | Joint | Draft v0.2 — both pairs to review |
| [architecture.png](architecture.png) | System overview for reports and presentations — generated from the Mermaid code in `architecture.md` | Joint | Draft v0.2 |
| [architecture-wol.png](architecture-wol.png) | Optional Wake-on-LAN path — generated from the Mermaid code in `architecture.md` | Joint | Draft v0.2 |
| [render-diagrams.sh](render-diagrams.sh) | Regenerates both PNG files from `architecture.md` (needs Mermaid CLI `mmdc`) | Joint | — |
| [data-design.md](data-design.md) | Sensor IDs, topics, JSON contract, publishing behaviour, alerts, ingestion and MQTT ACLs | Joint (PL draft) | Draft v0.2 — Philippines approval required |
| [vpn-plan.md](vpn-plan.md) | WireGuard addressing, service exposure, firewall rules, key lifecycle and time synchronisation | Joint (PL draft) | Draft v0.2 — Philippines input required |

## Baseline at a glance

| Area | Agreed design baseline |
| --- | --- |
| Sensors | Six simulated sensors: temperature, humidity and PM2.5 in each city |
| Publishing | One MQTT client per sensor; MQTT over TLS on `10.66.0.1:8883`; QoS 1; nominal interval 30 s |
| Network | WireGuard hub-and-spoke on `10.66.0.0/24`; only `51820/udp` is intentionally exposed by Project D to the internet |
| Data path | Simulators → Mosquitto → Telegraf → InfluxDB → Grafana |
| Access | Individual VPN profiles; faculty have dashboard access only; team members have the additional access assigned in the VPN matrix |
| Alerts | Reading outside the configured threshold and no reading for more than 120 s |
| Recovery | Nightly encrypted Restic backup to external storage; documented and timed fresh-VM restore test |
| Optional extension | Wake-on-LAN through a relay in the target LAN, only after all mandatory deliverables pass |

## Which document is authoritative?

- Use **data-design.md** for sensor names, topics, payload validation, QoS, alert definitions, ACLs and the meaning of `delay_ms`.
- Use **vpn-plan.md** for addresses, ports, firewall policy, profiles, keys and clock-synchronisation checks.
- Use **architecture.md** for component responsibilities and end-to-end behaviour. If a lower-level detail differs, the relevant data or VPN document takes precedence until the conflict is resolved by a reviewed pull request.
- Code and deployment configuration must implement the approved documents. Do not silently repair a conflict in code; update the affected design document in the same pull request.

The words **MUST**, **MUST NOT**, **SHOULD** and **MAY** are normative. Values in angle brackets, such as `<cloud-vm-public-ip>`, are placeholders and must never be replaced by secrets in Git.

## Recommended implementation and verification order

1. Approve the sensor contract and VPN plan; assign every peer and MQTT identity.
2. Configure WireGuard, host firewall rules and `DOCKER-USER`; verify both allowed and denied access.
3. Deploy Mosquitto, Telegraf, InfluxDB and Grafana with persistent volumes, health checks and no public service bindings.
4. Run both simulators and validate payloads, per-sensor ACLs, retained status and reconnect behaviour.
5. Test both alerts, message-delay reporting and VPN interruption recovery; retain sequence and clock evidence before querying InfluxDB. Raw subscriber or broker logging is recommended only for optional duplicate analysis.
6. Configure nightly backup and perform a timed restore onto a fresh VM.
7. Link dated results from `tests/` to `evidences/` and the minimum-requirements checklist.
8. Start WoL or other extensions only after the mandatory path and restore test pass.

## Review and change process

1. Discuss changes in a pull request or a linked issue; include the reason and compatibility impact.
2. A document moves from **Draft** to **Approved** when at least one member of each pair has approved it.
3. Payload, topic, address or firewall changes require a compatibility check against simulators, Mosquitto ACLs, Telegraf, Grafana and tests.
4. Never commit private keys, passwords, tokens, real peer configurations or unredacted exported client profiles.
5. After approval, update dependent configuration and evidence in the same change where practical. Record intentional differences as known limitations rather than leaving them implicit.

Role allocation remains open. “Joint (PL draft)” records document authorship, not an agreed division of implementation or testing responsibilities.

## Main clarifications in v0.2

- Separated end-to-end ingestion delay from pure network latency and defined how clock uncertainty must be reported.
- Clarified that InfluxDB point overwriting cannot count QoS 1 duplicates; optional duplicate analysis should use pre-ingestion or client/broker evidence when available.
- Added bounded offline-queue, restart and sequence-number requirements for interruption tests.
- Added key revocation, safe firewall rollout, service start ordering and backup/restore acceptance checks.
- Kept optional WoL outside the mandatory delivery path and made its local-LAN relay requirement explicit.
- Diagrams: Mermaid in `architecture.md` is the single source; PNG files are generated by `render-diagrams.sh`.
- Added the read-only `test-observer` MQTT account so security tests never need Telegraf's production password.
- `DOCKER-USER` rules are re-applied by a systemd unit after Docker starts instead of `netfilter-persistent save`.
