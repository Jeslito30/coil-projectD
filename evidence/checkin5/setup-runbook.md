# Project D — Deployment and Restore Runbook

**Check-in 5 evidence package — status as of 23 November 2026**

Purpose: deploy Project D on a new server, restore data and configuration from restic/B2, measure the restore time, and retain reproducible evidence.

> Never add secrets to Git, screenshots, logs, or chat. Mark a task complete only when its observed result and evidence are available.

## Current standalone status

Server `<standalone-server>` (Ubuntu 24.04) runs commit `b0414de7ea38f8ab7dea5da778ff7f7e03b45188`. Earlier local fixes were replaced by that commit without `git clean`, volume deletion, or changes to existing secrets.

Verified on the standalone server:

- repeated `deployment/standalone/deploy.sh --endpoint <standalone-server>`: `DEPLOY_STATUS=0`, 47 seconds; this was a **redeployment, not a from-scratch installation measurement**;
- fresh data from 6/6 sensors; Grafana and Team Dashboard healthy; MQTT over TLS working;
- only SSH `22/tcp` and WireGuard `51820/udp` listen publicly; project services bind to `10.66.0.1` or loopback;
- 56/56 Ubuntu unit tests passed, together with Python compilation, shell syntax checks, and `docker compose config -q`;
- isolated network-namespace firewall test: 28/28 passed;
- Alert 2 for all three Szczecin sensors and recovery of all six alert instances to `Normal` passed; see section 8;
- `delay_ms` analysis completed for 444 normal-operation samples and a separate recovery window; see section 9.

The standalone server uses **new secrets and WireGuard keys** and an independent peer registry at `/root/projectd-profiles/peers.md`. It is not a restore of production data or configuration from B2. Do not use the production QR profiles as profiles for this server.

## 1. Prerequisites

- a new Ubuntu/Debian server with working root SSH access;
- a second terminal session to confirm that the firewall does not lock out SSH;
- Jeslito's root access to Contabo, where `/etc/projectd/backup.env` is stored;
- access to the B2 bucket and the restic password;
- the new server address and a working SSH key;
- the project repository, including the deployment and backup READMEs.

## 2. Copy setup scripts to the new server

From the prepared setup directory in Git Bash:

```bash
NEW_SERVER='root@NEW_SERVER_ADDRESS'
ssh "$NEW_SERVER" 'mkdir -p /root/projectd-setup && chmod 700 /root/projectd-setup'
scp server/*.sh "$NEW_SERVER:/root/projectd-setup/"
ssh "$NEW_SERVER" 'chmod 700 /root/projectd-setup/*.sh'
```

After copying scripts from Windows, normalize line endings to LF:

```bash
ssh "$NEW_SERVER" 'sed -i "s/\r$//" /root/projectd-setup/*.sh'
```

## 3. Firewall and updates

```bash
ssh "$NEW_SERVER"
cd /root/projectd-setup
bash ./01-firewall-updates.sh
```

The script:

- detects the effective SSH port (`SSH_PORT=2222` can override it);
- allows SSH and WireGuard UDP/51820 before enabling UFW;
- sets incoming traffic to deny and outgoing traffic to allow by default;
- installs automatic security updates and runs `full-upgrade`;
- writes a log under `/root/projectd-evidence/`;
- does not reboot automatically.

**Before closing the first session, open a second one and verify `ssh "$NEW_SERVER"`.** If the system reports `REBOOT_REQUIRED=yes`, schedule a reboot and verify SSH and UFW again afterwards.

Published Docker container ports can bypass UFW. After starting services, inspect `docker ps`, `ss -lntup`, and actual external reachability. The repository test `tests/security/firewall-netns-test.sh` requires `socat` and `iproute2`.

## 4. Install Docker and WireGuard

```bash
cd /root/projectd-setup
bash ./02-docker-wireguard-install.sh
```

The script installs Docker from the official repository, Docker Compose, WireGuard, restic, and qrencode. It enables IPv4 forwarding. If `/etc/wireguard/wg0.conf` exists, it starts `wg-quick@wg0`.

If that configuration is absent, the script **does not generate new keys** during restoration. Restore the previous `/etc/wireguard` first to preserve existing QR profiles. Generating new keys requires reissuing profiles to faculty and clients.

For the standalone deployment with new keys, run:

```bash
cd /opt/coil-projectD
bash deployment/standalone/deploy.sh --endpoint <standalone-server>
```

After a server reboot, Docker should start after `wg-quick@wg0` through `deployment/contabo/docker-after-wireguard.conf`. A complete reboot → Docker → WireGuard → application test has not yet been performed.

## 5. Transfer restic/B2 credentials securely from Contabo

The source file is `/etc/projectd/backup.env` **on Contabo**. Jeslito has root access there. Do not substitute addresses found in `known_hosts` or terminal history for Contabo. `bilewski.am.szczecin.pl:40071` is not a Project D server.

Do not paste the file contents into chat. After agreeing access, Jeslito runs these commands **on Contabo**:

```bash
NEW_SERVER='root@<standalone-server>'
ssh "$NEW_SERVER" 'mkdir -p /etc/projectd && chmod 700 /etc/projectd'
scp -p /etc/projectd/backup.env "$NEW_SERVER:/etc/projectd/backup.env"
ssh "$NEW_SERVER" 'chmod 600 /etc/projectd/backup.env'
```

If Contabo cannot connect directly to the new server over SSH, Jeslito should transfer the file through an agreed secure channel or write it directly on the new server.

Check only its presence and permissions; do not print its contents:

```bash
ssh root@<standalone-server> 'stat -c "%a %n" /etc/projectd/backup.env'
```

Expected output: `600 /etc/projectd/backup.env`.

## 6. Restore from B2 with timing — not yet performed

```bash
ssh "$NEW_SERVER"
cd /root/projectd-setup
bash ./03-restore-b2.sh /etc/projectd/backup.env
```

By default, the script restores snapshot `latest` into the empty directory `/srv/projectd-restore`. To select another snapshot or target:

```bash
RESTIC_SNAPSHOT_ID='SNAPSHOT_ID' RESTORE_TARGET='/srv/projectd-restore' \
  bash ./03-restore-b2.sh /etc/projectd/backup.env
```

The result is written to `/root/projectd-evidence/restore-result.txt`, including exit code, start time, end time, elapsed seconds, and HH:MM:SS. The log does not print `backup.env` or the process environment.

For Check-in 5, retain:

- snapshot ID/date and repository size from `restic snapshots` / `restic stats`;
- timing from `restore-result.txt`;
- `du -sh /srv/projectd-restore`;
- a screenshot or log showing successful completion;
- separately measured download, service-start, and full-readiness times when available.

`restic restore` alone proves file recovery, not a working application. Also test simulator → MQTT/TLS → Telegraf → InfluxDB → Grafana/dashboard. Record the result in `tests/restore/restore-log.md`; its empty template must not be presented as a completed restore.

## 7. Start the restored configuration and services

If the restored file exists as `/srv/projectd-restore/etc/wireguard/wg0.conf`:

```bash
install -m 600 /srv/projectd-restore/etc/wireguard/wg0.conf /etc/wireguard/wg0.conf
systemctl enable --now wg-quick@wg0
wg show wg0
```

Locate the restored project directory and follow its deployment and backup READMEs. Do not blindly start every Compose file: inspect variables, volumes, ports, Compose project name, and network names first. Before changing a running Contabo stack, use read-only `deployment/contabo/reconcile_check.py`.

Minimum standalone validation:

```bash
cd /opt/coil-projectD/deployment/contabo
docker compose ps
ss -lntup
wg show
curl -fsS http://127.0.0.1:13000/api/health
curl -fsS http://127.0.0.1:18088/healthz
systemctl is-active projectd-sim@mati projectd-sim@szczecin
```

## 8. Alert 2 and missing data

### Completed sensor-silence tests — 23 November 2026

Both runs stopped only `projectd-sim@szczecin`; `projectd-sim@mati` remained the control. Grafana alert-instance states were polled through the API every 5 seconds. A trap restarted the service if the test script failed.

| Event | First test UTC | Recorded test UTC |
| --- | --- | --- |
| Szczecin simulator stopped | 19:59:53 | 20:12:01 |
| All three Szczecin sensors `Alerting` | 20:01:49 | 20:13:47 |
| Simulator restarted | 20:02:34 | 20:14:32 |
| All six instances `Normal` again | 20:02:49 | 20:14:48 |
| Stop-to-alert elapsed time | 116 s | 106 s |
| Restart-to-full-recovery elapsed time | 15 s | 16 s |

The alert evaluates the **age of the latest reading**, not elapsed time since `systemctl stop`. The last reading before shutdown explains an observed delay below 120 seconds. The rule uses a 120-second threshold and is evaluated every 30 seconds. Mati remained `Normal` throughout.

First-test evidence: `alert2/summary.json`, `alert2/run.log`, and the `alert2-before.json`, `alert2-firing.json`, and `alert2-recovered.json` state files.

Recorded-test evidence: `alert2-recorded/summary.json`, `alert2-recorded/run.log`, `alert2-recorded-run.log`, and state JSON files under `alert2-recorded/`.

Visual evidence from the recorded test:

- Grafana: `browser/grafana-alert2-before.png`, `grafana-alert2-firing.png`, and `grafana-alert2-recovered.png`;
- Team Dashboard: `browser/team-dashboard-before.png`, `team-dashboard-firing.png`, and `team-dashboard-recovered.png`;
- full-length recordings: kept outside the repository (only the 8× versions are committed);
- 8× versions: `browser/grafana-alert2-8x.webm` and `team-dashboard-alert2-8x.webm`;
- capture control: `browser-capture.log`, with `CAPTURE_STATUS=PASS`.

SMTP is disabled, so **email delivery was not tested**. This test demonstrates Grafana and dashboard reactions to missing data; it does not replace a physical WireGuard interruption and subsequent handshake.

### Physical VPN interruption — still to be performed

Use an agreed maintenance window and an access path that will not be cut off:

```bash
cd /root/projectd-setup
bash ./04-vpn-break-test.sh wg0
```

By default, VPN is disabled for `120 + 30` seconds and then restored automatically. A trap also restores VPN if the script is interrupted. Retain the log, `vpn-test-result.txt`, Alert 2 evidence, a fresh handshake, and a post-recovery connectivity test.

The Szczecin-side laptop procedure additionally requires stopping the server-side Szczecin simulator before starting the laptop instance. The laptop simulator must remain one uninterrupted process throughout baseline, interruption, and queue drain: its queue is **memory-only**, and restarting the process discards queued readings. Mati remains active as the control.

## 9. `delay_ms` analysis — completed

`tests/delay/analyze_delay.py` ran in a one-shot container attached to the internal Docker network with an existing read-only token. InfluxDB was not exposed publicly. Raw InfluxDB responses were retained as `*.raw.csv` beside the analysis results.

### Normal operation

Window: `2026-11-23T19:23:00Z`–`2026-11-23T19:59:50Z`; 444 samples.

| Scope | Count | Mean ms | Median ms | p95 ms | Min ms | Max ms | Negative |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Mati | 222 | 1.688 | 1.674 | 2.345 | 0.441 | 4.103 | 0 |
| Szczecin | 222 | 1.274 | 1.293 | 1.944 | 0.378 | 2.315 | 0 |

### Recovery

Window: `2026-11-23T20:02:34Z`–`2026-11-23T20:05:00Z`; 30 samples.

| Scope | Count | Mean ms | Median ms | p95 ms | Min ms | Max ms | Negative |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Mati | 15 | 1.639 | 1.606 | 2.191 | 1.128 | 2.191 | 0 |
| Szczecin | 15 | 10.921 | 2.192 | 47.370 | 1.154 | 47.370 | 0 |

The recovery window is short. Szczecin p95 equals the maximum with five samples per sensor and must not be compared directly with the long normal-operation window.

`delay_ms` is Telegraf processing time minus the payload timestamp, **not pure network latency**. Both simulators and the full stack ran on the same server, so this result describes local processing rather than a link between independent hosts. `timedatectl timesync-status` before and after analysis showed NTP synchronization with a +2.584 ms offset.

Evidence: `delay-normal.raw.csv`, `delay-normal.csv`, `delay-normal.md`, `delay-normal-chart.png`, corresponding `delay-recovery.*` files, and `delay-analysis-run.txt`.

Production delay measurements through the real inter-host/VPN path remain open and must not be inferred from this local baseline.

## 10. Evidence and definition of done

| Item | Minimum evidence | Status |
| --- | --- | --- |
| Firewall | log, `ufw status`, second SSH session, rule test | Complete; 28/28, `firewall-netns-b0414de.txt` |
| Updates | apt log, reboot, and new SSH session | Completed on 22 November |
| Docker and stack | Compose status, health checks, 6/6 sensors | Complete; `redeploy-b0414de.txt` |
| Repository tests | Ubuntu result without skips | 56/56; `offline-tests-linux-b0414de.txt` |
| WireGuard | `wg show`, handshake, and ping through tunnel | Server and laptop profile ready; laptop handshake not confirmed |
| B2 restore | exit code 0, snapshot, timing, functional test | **Not performed; `backup.env` unavailable** |
| Alert 2 — missing data | API log, alert, and recovery | Complete for a stopped simulator |
| VPN interruption | interruption log, alert, new handshake | **Not performed** |
| `delay_ms` | raw export, CSV, report, chart, and time window | Complete for the local standalone baseline |
| Dashboard and Grafana | screenshots/recording of the working system | Complete; screenshots and recordings under `browser/` |

## 11. Dependencies and follow-up

- Production MQTT broker fix: awaiting Jeslito's confirmation; publish `held-security` and its audit report only afterwards.
- Production Team Dashboard and optional B2 mirror: Jeslito.
- Faculty WireGuard QR profiles: send only after confirming key compatibility; standalone uses new keys.
- Team accounts: create after receiving public keys; do not grant everyone root/sudo/docker access.
- Faculty access evidence requires a real VPN login/handshake and a successful read-only application session, not merely a generated QR profile.
- Email alerts remain unverified on standalone because SMTP is disabled. If email notification is a minimum requirement, Jeslito must demonstrate it on production.
- Contabo firewall and public Coolify/Supabase exposure are **reported risks requiring owner verification**, not findings independently verified in this session. Record the observed configuration and remediation in the security report.
- Peer evaluation: document task assignments, deadlines, responses, and missing responses on Discord without speculating about motivation.
