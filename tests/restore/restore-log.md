# Project D — fresh-VM restore protocol

**Restore operator (role TBD):**  
**Start UTC:**  
**End UTC:**  
**Measured duration:**  
**Restic snapshot ID / timestamp:**  
**Repository (redacted identifier only):**  
**Source backup log:**  
**Fresh VM OS / Docker / Compose versions:**  
**Project commit and image digests:**  

## Preconditions

- [ ] Fresh isolated VM; no production data worth preserving.
- [ ] External repository accessible and credentials recovered through the approved secret process.
- [ ] Project configuration and required secrets available outside Git.
- [ ] `backup/restore.sh` first run without `--apply` to inspect the snapshot.
- [ ] Snapshot contains InfluxDB, Grafana and configuration data.

## Commands and timeline

| UTC | Action / command (no secrets) | Exit status | Notes |
| --- | --- | --- | --- |
| | Snapshot extraction | | |
| | `--apply --yes` started | | |
| | InfluxDB ready | | |
| | InfluxDB restore finished | | |
| | Grafana restore finished | | |
| | Compose stack started | | |
| | Validation finished | | |

## Acceptance checks

| Check | Expected | Observed / evidence | Pass? |
| --- | --- | --- | --- |
| Container health | All required services healthy/running | | |
| Historical InfluxDB points | Both cities present through backup timestamp | | |
| Grafana | Login works; datasource, dashboard and two alert definitions present | | |
| Access control | No new public binding; VPN-only access restored | | |
| New ingestion | One new valid test reading appears after restore | | |
| Backup schedule | Timer/configuration reviewed; next backup can run | | |

## Data-loss and recovery assessment

- Last successful source backup time:
- Newest historical reading restored:
- Observed data-loss window:
- Measured restore duration:
- RPO/RTO statement based only on these observations:

## Errors, manual intervention and follow-up

Record every error and manual fix; a restore requiring undocumented intervention is not a clean pass.

**Final result:** PASS / FAIL / PARTIAL  
**Evidence links:**  
**Retest required / date:**
