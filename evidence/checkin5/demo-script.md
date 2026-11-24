# Project D — Demo Recording Script

**Target length: approximately 6–8 minutes**

**Status as of 23 November 2026**

This script covers the standalone deployment and testing evidence. The final group presentation still needs the Mati, production, and faculty-access sections from Jeslito and qwerty_l3y. Do not present this standalone-only material as the complete PL/PH system demonstration.

## Material already recorded

A real Alert 2 test was recorded by stopping the Szczecin simulator while Mati remained the control:

- `browser/grafana-alert2-8x.webm` (8× speed; the full-length recording is kept outside the repository);
- `browser/team-dashboard-alert2-8x.webm` (8× speed; the full-length recording is kept outside the repository);
- `before`, `firing`, and `recovered` screenshots for both applications under `browser/`;
- `alert2-recorded/run.log` and `browser-capture.log` (`CAPTURE_STATUS=PASS`).

This material shows **sensor silence**, not a physical WireGuard interruption. SMTP was disabled, so it does not show email delivery. The B2 restore remains incomplete and must not be replaced with footage of a redeployment.

## Before final editing

- use the existing Alert 2 recording and the English chart `delay-normal-chart-en.png`;
- after the B2 restore, add the new-server terminal, snapshot identity, timing, and functional validation;
- after the physical VPN test, add `wg show`, the interruption, Alert 2, and the new handshake;
- retain backup screenshots of every key result;
- hide tokens, passwords, private keys, and the complete `backup.env`;
- label each item as standalone or production and state the measurement path;
- add the production/Mati/faculty footage from Jeslito and qwerty_l3y before calling the recording a group demo.

## Recording sequence

### 1. Objective and architecture — 30 seconds

Show the path: simulator → MQTT/TLS → Telegraf → InfluxDB → Grafana and Team Dashboard. Mention WireGuard and the restic/B2 backup design, explicitly marking the B2 restore as not yet performed.

Suggested narration:

> Project D collects readings from Szczecin and Mati over authenticated MQTT with TLS. Telegraf writes them to InfluxDB, and Grafana and the read-only Team Dashboard present live state and alerts. This section demonstrates the standalone deployment and its measured tests; production and faculty access are covered separately.

### 2. Live data — 60–90 seconds

Show a new reading and correlate the same sensor identifier and timestamp in InfluxDB and on the dashboard. Use `browser/team-dashboard-before.png` as a fallback still, but retain a fresh timestamp in the final live footage.

Suggested narration:

> All six sensor streams are present. The same reading is visible in the database and on the dashboard, demonstrating the ingestion path rather than only a static interface.

### 3. Delay analysis — 45–60 seconds

Show the `19:23:00–19:59:50Z` window, 444 samples, medians, p95 values, and the English chart.

Normal-operation results:

- Mati: median 1.674 ms, p95 2.345 ms;
- Szczecin: median 1.293 ms, p95 1.944 ms.

Suggested narration:

> These 444 samples are a local standalone baseline. `delay_ms` is Telegraf processing time minus the payload timestamp, not pure network latency. Both simulators and the broker ran on the same server, without a VPN crossing or a Philippines-to-Poland link. We therefore do not claim these numbers as end-to-end production latency. The short recovery window is reported separately and is not directly comparable with this longer baseline.

### 4. Alert 2 — 2–3 minutes, or the 8× recording excerpt

Show the recorded sequence:

1. before the test, all six alert instances are `Normal`;
2. Szczecin simulator stops at `20:12:01Z`;
3. all three SZCZ instances become `Alerting` at `20:13:47Z` — 106 seconds after the stop command;
4. the dashboard shows three SZCZ sensors as `Silent`, while Mati remains `OK`;
5. the simulator restarts at `20:14:32Z`, and all six instances are `Normal` at `20:14:48Z`.

Suggested narration:

> Alert 2 detects missing readings. The measured 106 seconds is consistent with the 120-second data-age threshold because the last reading predates the stop command and the rule is evaluated every 30 seconds. Mati stays normal as the control. Recovery completes 16 seconds after restart. This is a stopped-simulator test, not a physical VPN-drop test. Email delivery is not claimed because SMTP is disabled here.

If a physical VPN test is subsequently completed, present it as a separate segment with the interruption log and fresh handshake.

### 5. B2 restore — 60–90 seconds, only after the test is completed

Show the new server, selected snapshot, `RESTORE_STATUS=0`, measured duration, and final functional test. Clearly separate `restic restore` time from complete service-start and readiness time.

Suggested narration after a successful test:

> The selected snapshot was restored to an empty target with exit status zero. File restoration took the displayed time; full application readiness took the separately displayed time. We then verified historical data and one new reading through the complete ingestion path.

Until this test exists, use the on-screen label **NOT PERFORMED — awaiting `backup.env` from Contabo**. Do not substitute another success claim.

### 6. Security and close — 30–45 seconds

Show the 28/28 firewall result, public listeners limited to SSH and WireGuard, and 56/56 Ubuntu tests. Describe production MQTT hardening only according to Jeslito's confirmed status.

Suggested narration:

> The standalone server exposes only SSH and WireGuard publicly. Repository tests and the isolated firewall test pass. Production broker hardening, production dashboards, faculty access, and the B2 restore remain explicitly separated from these standalone results. We close with what is proven, the measurement limitations, and the remaining owners.

## Additional group segments required for the final demo

| Segment | Owner / dependency | Evidence required |
| --- | --- | --- |
| Mati over the real network path | Jeslito | live Mati ingestion, source host/path, and production delay results |
| Production MQTT hardening | Jeslito | deployed version/commit and negative tests for client-ID takeover and oversized packets |
| Production Team Dashboard | Jeslito | live dashboard and, if delivered, the optional public B2 mirror |
| Faculty access | Jeslito / qwerty_l3y / faculty | real VPN handshake and read-only Grafana/dashboard session for both faculty |
| Physical VPN interruption — Mati | Jeslito | tunnel drop, Alert 2, recovery, and fresh handshake |
| Physical VPN interruption — Szczecin | Darek | laptop tunnel drop, Alert 2, memory-only queue drain, and fresh handshake |
| Email alert, if required | Jeslito on production | sent alert and received email with sensitive headers redacted |

## Minimum fallback shots

1. `restore-result.txt` without secrets — after the B2 restore.
2. `wg show` before and after the physical VPN test — after that test.
3. `browser/grafana-alert2-firing.png` and `browser/grafana-alert2-recovered.png` — ready.
4. `browser/team-dashboard-before.png` and `browser/team-dashboard-firing.png` — ready.
5. `delay-normal.md` and `delay-normal-chart-en.png` — ready.
6. `firewall-netns-b0414de.txt` and `post-redeploy-health-b0414de.txt` — ready.
7. Production and faculty-access captures listed above — pending their owners.

## Final editorial checks

- Every number matches its linked report and time window.
- Local processing delay is never described as production network latency.
- Sensor silence is never described as a physical VPN interruption.
- A redeployment is never described as a fresh-machine B2 restore.
- Standalone results are never presented as production results.
- Missing email, restore, VPN, production-delay, and faculty-access evidence is explicitly labelled open.
- No passwords, tokens, private keys, complete profiles, or environment secrets appear in frames or file metadata.
