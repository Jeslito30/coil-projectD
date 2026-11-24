# Project D — Minimum Requirements and Evidence Matrix

**Status as of 23 November 2026**

**Standalone server:** `<standalone-server>`

**Baseline deployment commit:** [`b0414de`](https://github.com/dtsulikowski-glitch/coil-projectD/commit/b0414de7ea38f8ab7dea5da778ff7f7e03b45188)

Unless a row explicitly says production, its status applies only to the standalone server, not Contabo. “Ready” means the linked result exists; a script or template alone is not proof of execution.

## Minimum requirements and proof

| Requirement | Evidence | Status and limitation |
| --- | --- | --- |
| Repeatable standalone deployment | [Deployment log](redeploy-b0414de.txt): `DEPLOY_STATUS=0`, 47 seconds, 6/6 sensors; [post-deployment health check](post-redeploy-health-b0414de.txt) | **Complete as a redeployment.** Not a from-scratch installation measurement or a B2 restore. |
| Restore on a clean machine from B2, with timing | [Restore protocol/template](../../tests/restore/restore-log.md); [procedure](setup-runbook.md#6-restore-from-b2-with-timing--not-yet-performed) | **Open — `/etc/projectd/backup.env` from Contabo is unavailable.** Required proof: exit code 0, snapshot ID, measured duration, restored-data inventory, and functional test. |
| Server hardening and firewall | [Network-namespace test](firewall-netns-b0414de.txt): 28/28; [listener and health check](post-redeploy-health-b0414de.txt): publicly only SSH and WireGuard | **Complete on standalone, 22–23 November.** Contabo exposure is a separate, owner-verification item below. |
| Repository validation | [Ubuntu test output](offline-tests-linux-b0414de.txt): 56/56 without skips; Python compilation, shell syntax, and Compose validation | **Complete for the recorded checks.** This does not claim that every CI workflow check passed. |
| WireGuard server configuration | [Post-deployment health check](post-redeploy-health-b0414de.txt); [deployment/runbook status](setup-runbook.md#current-standalone-status) | **Server configured.** Laptop profile exists, but a real laptop handshake and ping are **not confirmed**. |
| Alert 2 when sensor data stops | [Recorded-test summary](alert2-recorded/summary.json), [test log](alert2-recorded/run.log), [Grafana firing](browser/grafana-alert2-firing.png), [Grafana recovered](browser/grafana-alert2-recovered.png), [Team Dashboard silent](browser/team-dashboard-firing.png) | **Complete for a stopped simulator.** Alert after 106 seconds; full recovery 16 seconds after restart; Mati remained Normal. Not a physical VPN-drop test. |
| Alert 2 after a physical VPN interruption | [VPN test requirements](setup-runbook.md#physical-vpn-interruption--still-to-be-performed) | **Open.** Required: tunnel interruption, Alert 2, recovery, fresh handshake, and connectivity. Szczecin and Mati sides are separate evidence items. |
| Delay analysis | [Normal-operation report](delay-normal.md), [raw export](delay-normal.raw.csv), [analysis CSV](delay-normal.csv), [English chart](delay-normal-chart-en.png), [recovery report](delay-recovery.md) | **Local standalone baseline complete:** 444 normal and 30 recovery samples. `delay_ms` is not pure network latency; both simulators were local. |
| Production/inter-host delay through VPN | Jeslito, Discord #group-10, 24 Nov 2026 (CSV `mati_normal_2026-11-24.csv`, to be added here) | **Started — Mati only.** 66 readings (22 per sensor) over the real Mati laptop → WireGuard → Contabo MQTT TLS path, measured by a separate read-only subscriber: mean 127.5–131.8 ms, p95 138.7–147.7 ms per sensor. MQTT reception delay including clock offset, not pure network latency. Szczecin real-path figures still open. |
| Team Dashboard and Grafana | [Dashboard before test](browser/team-dashboard-before.png), [Dashboard Silent](browser/team-dashboard-firing.png), [Grafana Firing](browser/grafana-alert2-firing.png) | **Standalone works.** Production deployment and the optional public B2 mirror remain with Jeslito. |
| MQTT with TLS | [Post-deployment health check](post-redeploy-health-b0414de.txt) | **TLS verified on standalone.** This row does not prove production client-ID and packet-limit hardening. |
| Production MQTT hardening: client-ID takeover and packet-size limit | Jeslito, Discord #group-10, 24 Nov 2026 (screenshots); [audit remediation status](../../documents/security-audit-2026-11-19.md#remediation-status--24-nov-2026) | **Deployed on production 24 Nov:** Mosquitto 2.1.2 with `use_username_as_clientid true` and `max_packet_size 16384`, ACL `1883:1883`/`640`, negative ACL test passed. A direct client-ID takeover / 1 MB test against production is still recommended. |
| Email alert notification | [Standalone SMTP limitation](setup-runbook.md#completed-sensor-silence-tests--23-november-2026) | **Not demonstrated on standalone; SMTP is disabled.** If email is a minimum requirement, Jeslito must supply production send/receive evidence. Earlier check-in claims do not replace final evidence. |
| Faculty VPN profiles and read-only access | [Access evidence requirements](setup-runbook.md#11-dependencies-and-follow-up) | **Open.** Generated QR profiles are not proof of use. Required: real handshakes and successful read-only sessions for both faculty, without exposing private keys. |
| Setup guide and runbook | [English setup and restore runbook](setup-runbook.md) | **Complete as a draft with explicit open items.** The restore section will be updated after a real B2 run. |
| Demo scenario | [English demo recording script](demo-script.md) | **Complete as a script.** Final group footage still needs production, Mati, and faculty-access sections. |
| Alert 2 demo recordings | [Grafana 8×](browser/grafana-alert2-8x.webm), [Team Dashboard 8×](browser/team-dashboard-alert2-8x.webm), [capture log](browser-capture.log) | **Recordings ready.** The final group video requires editing and must not imply completion of B2 or physical VPN tests. |
| Evidence-based peer evaluation | [Documentation rule](setup-runbook.md#11-dependencies-and-follow-up) | **Process defined.** Retain dated Discord assignments, reminders, responses, and artefact/commit links; describe observable contributions only. |

## Code, production, and security follow-up

| Item | Evidence / action required | Current status |
| --- | --- | --- |
| PR #3 | [Recorded Ubuntu tests](offline-tests-linux-b0414de.txt) and [firewall tests](firewall-netns-b0414de.txt) | More than 60 commits; 56/56 and 28/28 recorded. **Merge remains Jeslito's decision.** No merge performed here. |
| GitGuardian red check | Repository maintainer must inspect and classify reported template/example values in GitGuardian | **Open maintainer action.** No claim is made here that the check has turned green. |
| `held-security` publication | Commits `0064e93`, `36bb841`, `27d2eb0` | **Published 24 Nov** after Jeslito's production confirmation. |
| Production Team Dashboard | Live production URL through VPN and evidence of the optional B2 mirror if delivered | **Jeslito's task; open in this package.** |
| Contabo firewall and public Coolify/Supabase ports | Owner-controlled configuration review, external port evidence, and remediation record | **Reported risk, not independently verified in this session.** Keep separate from verified standalone firewall results. |
| Full reboot ordering | Reboot → WireGuard → Docker → application health and ingestion | **Open.** The Docker ordering override exists, but the complete reboot test has not been recorded. |
| B2 restore log | [Empty protocol](../../tests/restore/restore-log.md) | **Intentionally unfilled.** Complete only from observed restore output. |

## Evidence handling rules

- Do not mark readiness from the existence of a script alone.
- Do not expose passwords, tokens, private keys, complete WireGuard profiles, or `backup.env` in evidence.
- Keep standalone and production results distinct.
- A stopped-simulator alert is not a physical VPN interruption.
- A redeployment is not a clean-machine restore.
- Local processing delay is not production network latency.
- A generated VPN profile is not evidence that faculty accessed the system.
