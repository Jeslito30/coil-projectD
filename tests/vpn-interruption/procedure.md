# VPN interruption and sequence test — Project D

## Purpose and acceptance

Demonstrate reconnect and ingestion recovery after a simulator's VPN interruption. Required analysis covers sequence gaps, simulator restarts and queue overflow. Duplicate analysis is recommended only when suitable pre-ingestion logs exist; it is not a mandatory acceptance gate.

Do not claim zero loss merely because InfluxDB contains one point per timestamp. QoS 1 duplicates may collapse during storage, and a sequence reset can look like duplication if restarts are not separated.

## Preparation

- Test operator and second observer: **TBD — role allocation pending**.
- Simulator host / peer: ____________________.
- Server and simulator software revisions: ____________________.
- Test start/end UTC: ____________________.
- Publish interval and offline queue capacity/persistence/overflow policy: ____________________.
- Fresh simulator `run_id`: ____________________.
- Approved interruption method and restoration owner: ____________________.

Record clock evidence before the test:

```bash
vpn/ntp/chrony-check.sh tests/vpn-interruption/results/chrony-before.md
```

Run it on the server and every participating simulator host. For long or virtualised runs, repeat afterwards. Start from a fresh simulator run or assign a new `run_id`; record every process restart.

## Procedure

1. **Baseline (at least 5 minutes):** confirm all six sensors publish, their sequence values increase and current points arrive in InfluxDB. Save the exact query window.
2. **Start outage:** disable only the simulator host's `wg0` using the approved method. Record UTC immediately. Do not reboot the simulator or change its `run_id` during the same test run.
3. **During outage (at least 3 minutes or the approved test window):** keep generating readings. Record local queue utilisation, drops/overflow and application errors. A shorter outage than the queue capacity does not test overflow.
4. **Restore VPN:** re-enable `wg0`, record handshake and reconnection UTC. Do not manually recreate Docker services.
5. **Recovery:** wait until the bounded queue drains or its terminal behaviour is clear. Record the last queued reading, first new reading and any dropped sequence ranges.
6. **Repeat clock check** where required and export chronological rows containing `sensor_id`, `seq`, `timestamp_utc` and `run_id`.
7. Analyse:
   ```bash
   python tests/vpn-interruption/check_seq.py chronological-export.csv \
     --output tests/vpn-interruption/results/sequence-analysis.json
   ```
8. Run delay analysis separately for normal and recovery windows with `tests/delay/analyze_delay.py`; do not mix queued readings into normal-operation percentiles.

## Optional duplicate observation

If raw subscriber, broker or client acknowledgement logs are already available, analyse repeated `sensor_id` + `run_id` + `seq` before InfluxDB storage and identify the evidence source. If no such logger exists, mark duplicate analysis **Not performed — recommended, no assigned component**. Do not add a new mandatory logging component solely for this test.

## Result record

| Item | Result |
| --- | --- |
| Baseline window UTC | |
| VPN down / up UTC | |
| Outage duration | |
| Handshake / MQTT reconnect UTC | |
| Queue capacity / maximum used | |
| Queue overflow or dropped ranges | |
| Simulator restarts / run IDs | |
| Sequence gaps and missing values | |
| Out-of-order events | |
| Advisory duplicates + evidence source, if available | |
| First fresh post-recovery point | |
| Clock offsets before/after | |
| Delay report paths (normal / recovery) | |
| Errors and limitations | |
| Pass / fail against reconnect and gap criteria | |

Store redacted evidence with the date in `tests/vpn-interruption/results/` and link it from the requirement checklist. Never include passwords, private keys or tokens.
