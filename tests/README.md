# Project D — test skeleton

Project D core tests cover delay, VPN interruption/sequence gaps, MQTT security and restore evidence. Malformed-payload validation and duplicate analysis from raw pre-ingestion logs are **recommended**, not mandatory assigned components.

## Setup

Use a virtual environment outside Git and install `python -m pip install -r tests/requirements.txt`. The delay and sequence scripts use only the Python standard library; the MQTT security test needs `paho-mqtt`.

Store passwords/tokens in environment variables, never in commands saved to Git or in result files. Result directories contain `.gitkeep`; add generated evidence deliberately after redacting secrets.

## Delay statistics

`delay/analyze_delay.py` queries InfluxDB's Flux API and writes CSV + Markdown with count, mean, median, p95, minimum and maximum by city and sensor. InfluxDB has no VPN/host binding, so run the script from an approved analysis container attached to the internal Docker network (`INFLUX_URL=http://influxdb:8086`) or another explicitly approved administrative path. Do not expose port 8086 just to run the test.

```bash
export INFLUX_TOKEN=<read-token>
python tests/delay/analyze_delay.py \
  --bucket coil --org coil --start -1h --scenario normal
```

Use separate, recorded time windows/scenarios for normal operation and recovery traffic. Record clock offsets with `vpn/ntp/chrony-check.sh` before and after the run.

## VPN interruption and sequences

Follow `vpn-interruption/procedure.md`, then analyse a CSV, JSON or JSONL export:

```bash
python tests/vpn-interruption/check_seq.py input.csv \
  --output tests/vpn-interruption/results/sequence-analysis.json
```

Required input fields: `sensor_id`, `seq`. Optional: `timestamp_utc`/`_time`, `run_id`. `run_id` is not stored in the payload or InfluxDB; add it to the export manually when a run is split, otherwise a return to `seq` 0 is reported as a `sequence_reset` event. Duplicate output is advisory and describes only the supplied input; InfluxDB overwrites identical points and cannot prove that no QoS 1 duplicate occurred.

Exclude recorded test windows, such as the MQTT security test, so their test message is not counted as a restart:

```bash
python tests/vpn-interruption/check_seq.py input.csv \
  --exclude-window 2026-11-20T08:00:00Z,2026-11-20T08:00:30Z
```

## MQTT security

The seven tests are: valid credentials, wrong password, missing credentials, own-topic publish, other-topic publish rejection, plaintext connection rejection and untrusted-CA rejection. The observer verifies actual own/other-topic delivery instead of treating a local `publish()` call as broker proof. Authentication tests pass only on a broker CONNACK rejection and the CA test only on a certificate-verification error; timeouts are reported as inconclusive failures.

```bash
export MQTT_TEST_USERNAME=SZCZ-TEMP-01
export MQTT_TEST_PASSWORD=<sensor-password>
export MQTT_OBSERVER_USERNAME=test-observer
export MQTT_OBSERVER_PASSWORD=<test-observer-password>
export MQTT_CA=/secure/path/ca.crt
python tests/security/mqtt_security_test.py \
  --own-topic coil/szczecin/temperature/SZCZ-TEMP-01 \
  --other-topic coil/szczecin/humidity/SZCZ-HUM-01
```

- The observer is the dedicated read-only `test-observer` account from the ACL in `documents/data-design.md`. Do not reuse Telegraf's production password on a laptop.
- The own-topic check publishes **one real reading** with a value at the midpoint of the sensor's normal range, so no threshold alert fires. It uses `seq` 0; the script prints and saves `test_window_utc` — pass it to `check_seq.py --exclude-window` and leave it out of delay reports.
- The test actor uses the sensor's identity with a unique client ID. Stop that sensor's simulator during the test or accept that the broker sees two clients with the same username (allowed; client IDs differ).

Verified on 2026-11-04 against Mosquitto 2.0.18 with TLS, the ACL from `data-design.md` and paho-mqtt 2.2.0: 7/7 passed, and the broker log showed `Denied PUBLISH` for the other-topic case.

## Restore and network evidence

- Complete `restore/restore-log.md` for every timed fresh-VM restore.
- Use `security/port-scan.md` to compare cloud firewall, host listeners, Docker mappings and external results. UDP `open|filtered` is inconclusive without a real WireGuard handshake.
