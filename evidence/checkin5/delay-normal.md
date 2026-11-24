# Project D — delay analysis

- Generated UTC: `20261123T200726Z`
- Window: `2026-11-23T19:23:00Z` to `2026-11-23T19:59:50Z`
- Scenario: **normal** (separate normal and recovery runs)
- Metric: Telegraf processing time minus payload timestamp; not pure network latency
- p95 method: nearest-rank; negative values retained as clock diagnostics

| Scope | City / sensor | Count | Mean ms | Median ms | p95 ms | Min ms | Max ms | Negative |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| city | Mati | 222 | 1.688 | 1.674 | 2.345 | 0.441 | 4.103 | 0 |
| city | Szczecin | 222 | 1.274 | 1.293 | 1.944 | 0.378 | 2.315 | 0 |
| sensor | MATI-AIR-01 | 74 | 1.635 | 1.639 | 2.361 | 0.441 | 2.693 | 0 |
| sensor | MATI-HUM-01 | 74 | 1.767 | 1.784 | 2.346 | 1.075 | 2.442 | 0 |
| sensor | MATI-TEMP-01 | 74 | 1.663 | 1.566 | 2.345 | 0.955 | 4.103 | 0 |
| sensor | SZCZ-AIR-01 | 74 | 1.050 | 0.996 | 1.752 | 0.378 | 1.911 | 0 |
| sensor | SZCZ-HUM-01 | 74 | 1.212 | 1.143 | 1.933 | 0.493 | 2.058 | 0 |
| sensor | SZCZ-TEMP-01 | 74 | 1.561 | 1.539 | 2.148 | 0.636 | 2.315 | 0 |

Attach chrony offsets, simulator restart times and the exact query window to this report.
