# Project D security audit — 19 Nov 2026

**Scope:** repository at branch `szczecin-wol-vpn` (PR #3), file by file, plus the server facts that Jeslito reported on 19 Nov (`docker ps`, `iptables -S FORWARD`, `reconcile_check.py`, SSH/UFW/VNC status). No direct access to the server was used.
**Method:** configuration and code review. Every finding marked *reproduced* was demonstrated locally with the repository's own configuration: Mosquitto 2.0.18, network namespaces for the firewall, restic 0.16.4. Each one has a regression test that fails on the previous version.
**Not covered:** Coolify/Supabase internals, the Contabo account, client devices, Grafana/InfluxDB source code.

## Summary

| # | Severity | Area | Finding | Status |
| --- | --- | --- | --- | --- |
| 1 | High | Broker | Any authenticated sensor can take over Telegraf's MQTT client ID and repeatedly disconnect it. Ingestion stops for both cities. *Reproduced.* | Fixed in repo (`use_username_as_clientid`); **live broker fixed 24 Nov** (see Remediation status) |
| 2 | High | Firewall | `docker-user.sh` appended its DROP after Docker's `RETURN`, so it restricted nothing. *Reproduced.* | Fixed in repo; was never applied on Contabo |
| 3 | High | Host | Coolify publishes 6001/6002/8000/8080 on all IPv4/IPv6 interfaces; no provider firewall; UFW inactive; INPUT policy ACCEPT | Admin decision (outside Project D) |
| 4 | Medium | Broker | No packet size limit: a sensor can publish up to 256 MB to its own topic (Telegraf memory). *Reproduced.* | Fixed in repo (`max_packet_size 16384`); **live broker fixed 24 Nov** (see Remediation status) |
| 5 | Medium | Host | Docker is not ordered after `wg0`. With ports bound to 10.66.0.1, Mosquitto and Grafana may stay down after a reboot | Fixed in repo (systemd drop-in); **live host: install the drop-in** |
| 6 | Medium | Backup | The server can delete its own snapshots (`restic forget --prune`); the host also runs internet-facing Coolify | `RESTIC_APPEND_ONLY=1` added; needs an append-only backend |
| 7 | Medium | Grafana | 12.1.1 is behind several 12.1.x security releases (up to `v12.1.10+security-01`) | Upgrade on the dedicated VPS after reading the release notes |
| 8 | Medium | InfluxDB | Telegraf and Grafana use the operator token (full read/write/delete) | `scoped_tokens.py` added; applies with the repo compose file |
| 9 | Medium | Ingestion | A valid sensor account could write another sensor ID, city or type in its payload and create new series | Fixed in repo (Telegraf registry check) |
| 10 | Medium | Host | Contabo VNC console is enabled | Disable once SSH over VPN is confirmed (keep as recovery until then) |
| 11 | Low | Firewall | `ufw-rules.sh` allowed SSH from the unassigned `.11` instead of `.10` | Fixed; do not enable UFW on the shared host yet |
| 12 | Low | Deployment | `telegraf:1.35.5` does not exist; the firewall unit used `/opt/project-d`, the backup unit `/opt/coil-projectD` | Fixed |
| 13 | Low | Backup | The WireGuard server key was not in the backup | Fixed (encrypted snapshot only) |
| 14 | Low | Repository | `evidence/checkin1` screenshots show the server IP, the VNC address and port, and a last-login IP; they stay in Git history | Crop/remove; treat the values as public |
| 15 | Info | Secrets | All 5 GitGuardian findings are variable names, `${...:?}` placeholders or a test string | No rotation needed; mark as false positives |
| 16 | Info | Tokens on argv | `backup.sh`, `inventory.py` and `scoped_tokens.py` pass the operator token as a CLI argument (visible in `ps` to local users) | Acceptable on a single-admin host; revisit if more users get shell access |

## Details

### 1. MQTT client-ID takeover (High)
Mosquitto disconnects an existing session when another client connects with the same client ID, whatever its username. Using its own valid password, `SZCZ-TEMP-01` connected as `telegraf-ingest`. Telegraf and the intruder then disconnected each other in a loop: the broker log shows *"Client telegraf-ingest already connected, closing old connection"* alternating between users `telegraf` and `SZCZ-TEMP-01`. Ingestion stops while this runs.
**Fix:** `use_username_as_clientid true` on the listener, so the broker replaces every client ID with the username. Simulators, the WoL relay and Telegraf already use their username as client ID. Consequence: one live connection per account.
**Test:** `tests/security/broker_abuse_test.py`, run by `docker/mosquitto/scripts/local-test.sh`. It fails on the old configuration and passes on the new one.
**Live stack:** add the two lines from findings 1 and 4 to the running `mosquitto.conf` and restart Mosquitto. Clients reconnect, and QoS 1 readings queued on the simulators are resent.

### 2. Firewall DROP after RETURN (High)
Details and the namespace test are in `documents/vpn-plan.md` §4.4. Rules now match the published host port (`--ctorigdstport`), and CI asserts that every published port is bound to 10.66.0.1 or 127.0.0.1, which covers IPv6. On the shared host the script only affects traffic arriving on `wg0`.

### 3. Coolify exposure (High, outside Project D)
`docker ps` shows Coolify and its proxy publishing on all interfaces; the INPUT policy is ACCEPT and there is no provider firewall. A Contabo firewall rule that limits 8000 (the panel) to the admin's address or the VPN closes most of the exposure without touching Docker. This is the main security argument for the dedicated Project D VPS.

### 4. Oversized packets (Medium)
A 1 MB publish to a sensor's own topic was delivered to subscribers. With `max_packet_size 16384`, the sender is disconnected and nothing is delivered (same test file as finding 1).

### 5. Docker before WireGuard (Medium)
`deployment/contabo/docker-after-wireguard.conf` is a systemd drop-in. `reconcile_check.py` now reports when the ordering is missing.

### 6. Backup deletable from the server (Medium)
Use `rest-server --append-only` or a cloud key without delete rights, set `RESTIC_APPEND_ONLY=1`, and run retention from a trusted machine. Keep the restic password off the server: with the WireGuard key now in the snapshot, it protects the whole VPN.

### 7–10.
See the summary table. Findings 8 and 9 only take effect with the repository compose file, i.e. on the dedicated VPS, as agreed on 19 Nov.

## Verified as correct
- MQTT: TLS only (no 1883), anonymous access off, explicit per-sensor ACL, Telegraf read-only, certificate SAN covers `10.66.0.1` and `mosquitto`, `ca.key` kept off the server.
- Peer isolation: `-P FORWARD DROP` on the live host (Docker's default), one `/32` per peer, no PostUp ACCEPT rule.
- InfluxDB is not published on the host (live and repo).
- Grafana: sign-up and anonymous access off, `X-Frame-Options: deny` (live check), the datasource is not editable, and the dashboard has no template variables, so Viewers cannot inject Flux.
- SSH (live): key-only, password and keyboard-interactive logins disabled.
- `.env` and `wg0.conf` (live): root, mode 600.
- CI: `pull_request` (not `pull_request_target`), read-only token, actions pinned by SHA, gitleaks tree scan.
- WoL relay: replay protection, allowlist, rate limit and crash recovery implemented and tested (contract tests 1–9).

## Priority for the live `coil-checkin2` stack (before Check-in 4, no migration needed)
1. Mosquitto: add `use_username_as_clientid true` (inside the 8883 listener block) and `max_packet_size 16384`, then restart Mosquitto (findings 1, 4).
2. Install the Docker-after-WireGuard drop-in (finding 5).
3. Contabo firewall rule for the Coolify panel (finding 3).
4. Optional: `vpn/firewall/docker-user.sh` (affects only `wg0` traffic; `--remove` rolls it back).

Everything else is planned for the dedicated VPS.

## Remediation status — 24 Nov 2026

Published after the production broker was patched, so findings 1 and 4 no longer describe a live weakness.

- **Production (Contabo), reported by Jeslito on 24 Nov:** Mosquitto 2.1.2 running with `use_username_as_clientid true` and `max_packet_size 16384`; ACL file owned `1883:1883`, mode `640`; configuration validated, broker restarted, Telegraf reconnected, all three Mati sensors publishing to InfluxDB. A negative ACL test passed (an authenticated sensor publishing to a topic outside its ACL got `Not authorized`).
- **Still to evidence on production:** the ACL test confirms authorisation, not finding 1 itself. Running `tests/security/broker_abuse_test.py` against the production broker (client-ID takeover and 1 MB publish) closes findings 1 and 4 with a direct negative test.
- **Repository:** both fixes and their regression tests are in `docker/mosquitto/config/mosquitto.conf`, `deployment/contabo/telegraf.conf` and `tests/security/broker_abuse_test.py`.
- Findings 2, 3 and 5 keep their status above; the Contabo host exposure (finding 3) remains an owner decision outside Project D.
