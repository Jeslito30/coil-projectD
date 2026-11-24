# Progress report — Check-in 4 (Friday 20 November 2026)

Template from the COIL Check-in Guide, section 04. Each member checks their own line under *Who did what* before 09:00 PL / 16:00 PH.

| Item | Answer |
| --- | --- |
| Team, project and date | Group 10 (PL & PH), Project D, Two-City IoT Sensor Dashboard, 20 Nov 2026 |
| What we finished this week | MQTT over TLS 1.3 with one login per sensor and topic ACLs: anonymous access refused, wrong-topic publish refused. Grafana alerts for out-of-range and silent sensors, with Gmail SMTP notification tested. Faculty Viewer account created and verified read-only. Team Dashboard v1, a read-only live view on `10.66.0.1:8088`. Security review of the repository and server: firewall script fixed and tested with real packets, broker hardening, backup scripts with an encrypted off-server design. |
| What we will do next week | Message delay from each city through the VPN and the VPN drop test. Whole stack restored on a fresh VM (the dedicated Project D VPS) with the time it took. Draft setup guide and runbook. Demo rehearsal. |
| What we changed or replaced, and why | (1) Built the team dashboard as a separate read-only web page instead of reusing Grafana, so faculty get one live view with the same alert rules. (2) Replaced the firewall approach: appending a DROP to `DOCKER-USER` was ineffective behind Docker's `RETURN`; rules now live in their own chain, tested with real packets. (3) Plan to move Project D from the shared Contabo VM, which also runs other projects, to a dedicated VPS after this check-in, so "only WireGuard open" can be met and verified with Nmap. (4) One Mati simulator now runs from a laptop in the Philippines over WireGuard, not on the server, so the Philippines-to-server path is really used. |
| Who did what | **Jeslito:** Contabo VM and `coil-checkin2` stack, WireGuard server, MQTT TLS/ACL tests, Grafana alerts and SMTP, faculty Viewer account, server audit and reconciliation. **Darek:** Szczecin simulator, PR #3 (firewall fix, backup/restore scripts, deployment tools, CI), security review, Team Dashboard v1. **Mykyta:** Szczecin simulator host, VPN connectivity test, faculty access for Dr. Frankowski. **qwerty_l3y:** new device setup and WireGuard key, server cost planning. |
| Links | Repository: https://github.com/Jeslito30/coil-projectD (PR #3) · Evidence: `evidence/` · Team dashboard: http://10.66.0.1:8088 (VPN only) |

## Check-in 4 requirements (guide, Project D)

| Requirement | Status on 20 Nov | Proof to show |
| --- | --- | --- |
| MQTT with TLS, one login per sensor, access rules; a wrong topic is refused (task 8) | Met | Live: publish as `SZCZ-TEMP-01` to a Mati topic, then show the denial in the broker log |
| Grafana alerts for an out-of-range reading and a silent sensor (task 9) | Met | Live: `--anomaly` on one simulator; e-mail received |
| Viewer accounts for both faculty (task 9) | Partly: 1 of 2 verified | Grafana user list (no passwords on screen) |
| WireGuard profiles sent to both faculty before the check-in | Open: no faculty peer on `wg0` yet | Peer list with public keys only |
| Cloud VM hardened, only the WireGuard port open (task 10) | Not met on the shared VM: Project D ports are VPN-only, but other services on the same VM are public | External scan of the Project D ports; plan: dedicated VPS |
| Nightly backups of InfluxDB and Grafana (task 11) | Open | `backup.sh` + timer, first snapshot listed |
| The live team dashboard | Ready in the repo; to be deployed on the server | http://10.66.0.1:8088 |

## 5-minute talk (guide, section 01)

1. **0:00–0:30** — Team 10, Project D, speaker and backup speaker.
2. **0:30–3:00** — Finished this week, with live proof: refused wrong-topic publish → alert firing → Team Dashboard turning *Out of range* / *Silent*, the same event on both.
3. **3:00–4:00** — What we changed and why: points 1 to 4 above.
4. **4:00–5:00** — Next week: delay and VPN drop tests, restore on a fresh VM, draft documents, rehearsal. State openly what is still missing from this list.

Do not show passwords, tokens or private keys on screen.
