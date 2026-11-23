# Check-in 5 evidence — standalone test server (23 Nov 2026)

The production system stays on Contabo. These results come from a **second, independent server** that Darek deployed from this repository to prove the setup guide works on a clean machine and to run tests without touching production. It uses its own keys and secrets; none of them are in this folder.

Start here: [requirements and evidence matrix](requirements-evidence.md) · [setup and restore runbook](setup-runbook.md) · [demo recording script](demo-script.md) · standalone deck `Project-D-standalone-testing-EN.pptx` · **joint final draft** `Project-D-joint-final-draft.pptx` (13 slides; Mati, production and faculty-access slides are marked for their owners) · **rough-cut video** `Project-D-standalone-rough-cut.mp4` (70 s, captions, no narration; standalone evidence only).

## Done

| Item | Result | Evidence |
| --- | --- | --- |
| Clean-server deployment | `deployment/standalone/deploy.sh` on fresh Ubuntu 24.04: WireGuard, MQTT TLS + per-sensor accounts, Telegraf, InfluxDB, Grafana, Team Dashboard, both city simulators. `DEPLOY_STATUS=0`, 6/6 sensors with fresh readings. Re-run: 47 s | `redeploy-b0414de.txt`, `post-redeploy-health-b0414de.txt` |
| Hardening | SSH key-only, UFW: only 22/tcp and 51820/udp public. MQTT, Grafana, dashboard on VPN/loopback only. Scoped (least-privilege) InfluxDB tokens | `post-redeploy-health-b0414de.txt`, `firewall-netns-b0414de.txt` |
| Repo tests | 56/56 offline tests, 28/28 firewall network-namespace checks (commit `b0414de`) | `offline-tests-linux-b0414de.txt`, `firewall-netns-b0414de.txt` |
| Alert 2 (sensor silent) | Szczecin simulator stopped 20:12:01 UTC → all 3 SZCZ alerts Firing 20:13:47 (106 s; rule threshold = latest reading older than 120 s). Restart 20:14:32 → all 6 Normal 20:14:48 (16 s). Mati stayed Normal as the control | `alert2-recorded/`, `browser/` (screenshots, 8× videos) |
| `delay_ms` baseline | 444 samples, p95 Mati 2.345 ms, Szczecin 1.944 ms. **Single-host baseline only** (simulators on the same server, no VPN or Philippines link) — not system latency | `delay-normal.md`, `delay-normal-chart-en.png`, raw CSVs |
| Fixes found by the real run | Docker-after-WireGuard drop-in tracked, Grafana alert provisioning path, `wg syncconf` call in `add-peer.sh`; new `vpn/remove-peer.sh` and `deployment/standalone/add-member.sh` | commits `b0414de`, `8ebe6da` |

SMTP is disabled on this server, so email delivery is **not** covered by the Alert 2 test.

## Still open before the final (2 Dec)

| # | Task | Owner |
| --- | --- | --- |
| 1 | Restore from the B2 backup onto the clean server, with timing and a functional check | Jeslito sends `backup.env` server-to-server; Darek runs the restore |
| 2 | Physical VPN-drop test with Alert 2 and reconnection (Mati side, over the real link) | Jeslito |
| 3 | Real-path `delay_ms` (Mati → VPN → server): 7-day CSV export from production InfluxDB | Jeslito |
| 4 | Confirm the MQTT broker fix (client-ID takeover, packet-size cap) on production; then the audit report is published | Jeslito |
| 5 | Check-in 5 recording script and video | qwerty_l3y + Jeslito |
| 6 | Team accounts on the test server (VPN + Grafana): send your WireGuard **public** key (`wg pubkey` output) | everyone |
| 7 | Final presentation, requirements list with evidence links, peer evaluation | all |
| 8 | Merge PR #3 | Jeslito (repo owner) |
