# Sensor simulators — Project D

`projectd_simulator.py` implements the reading contract in `documents/data-design.md` (v0.2) for **either city**; the city and sensors come from the config file. The Szczecin simulator is run by the Poland pair (`szczecin/config.example.yaml`). The same code can run Mati with a Mati config, so both cities are guaranteed to produce identical message formats.

## What it does

| Requirement (data-design.md) | Implementation |
| --- | --- |
| One MQTT client per sensor (1.1, 4.1) | Username and client ID = sensor ID; own Last Will |
| Topics (2.1, 2.2) | `coil/szczecin/<type>/<id>`; status `coil/szczecin/status/<id>` |
| Reading JSON (3.1) | `schema_version`, `sensor_id`, `city`, `sensor_type`, `timestamp_utc` (ms, `Z`), `seq`, `value` (always float), `unit`, `simulated: true`; every reading is validated before publishing |
| QoS / retain (4.1) | Readings QoS 1, retain false; status QoS 1, retained `online` / `offline` (Last Will on crash) |
| Session (4.1) | MQTT 3.1.1, clean session `false` |
| TLS (4.1) | Verifies the broker certificate with the project `ca.crt`; verification is never disabled |
| Offline queue (4.2) | Bounded per sensor (`queue_limit`, default 2000 ≈ 16.7 h), ordered, **memory-only: lost if the simulator process restarts**; overflow logged as `queue_overflow` |
| Health after reconnect (4.2) | `publish_resumed` is logged only when the first reading is acknowledged after (re)connect |
| Options (4.4) | `--config`, `--anomaly`, `--silence`, `--interval` (+ helpers below) |
| Values (5) | Random walk inside the normal range, step ≤ maximum normal step |
| Alert 1 test (6.1) | `--anomaly` publishes a value above the threshold (temperature 25.0 C, humidity 105.0 %RH, PM2.5 95.0 ug/m3 for Szczecin), then normal values resume |
| Alert 2 test (6.2) | `--silence` stops readings while the MQTT connection stays up |

`seq` starts at 0 when the simulator starts and increases by 1 per generated reading. Silenced readings are not generated, so silence produces no `seq` gap; a restart resets `seq` to 0.

## Event log

Every run appends JSON lines to `log_file` (default `simulator-szczecin.jsonl`): `start` (interval, queue limit, persistence), `connected`, `disconnected`, `publish` (seq, value, timestamp, `sent` / `queued_offline` / `dropped_queue_full`, pending count), `puback`, `publish_resumed`, `queue_overflow`, `silence_start` / `silence_end`, `stop` (unacknowledged count and last `seq` per sensor). Keep this file with the VPN-interruption evidence: it is the "queue log" and "restart log" required by data-design.md 4.2 and 4.3.

## Setup (once)

Requires Python 3.10+ and an active WireGuard connection to `10.66.0.1`.

**Windows (PowerShell):**

```powershell
cd simulators
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy szczecin\config.example.yaml szczecin\config.yaml
```

**Linux / macOS:**

```bash
cd simulators
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp szczecin/config.example.yaml szczecin/config.yaml
```

Put the public `ca.crt` from the server admin next to `config.yaml` (or set `broker.ca_file` to its path).

Passwords come from the server admin through a private channel (never chat or Git). Either set environment variables:

```powershell
$env:MQTT_SZCZ_TEMP_01_PASSWORD = "..."
$env:MQTT_SZCZ_HUM_01_PASSWORD  = "..."
$env:MQTT_SZCZ_AIR_01_PASSWORD  = "..."
```

or save the three `MQTT_SZCZ_..._PASSWORD=...` lines in a local file such as `szczecin/secrets.env` (git-ignored) and pass `--secrets-file`.

## Run

Run from the `szczecin` folder so `ca.crt` and the log file resolve next to the config:

```bash
cd szczecin
# Check the payloads without a broker
python ../projectd_simulator.py --config config.yaml --dry-run --count 2 --interval 1
# Normal operation (30 s interval, until Ctrl+C)
python ../projectd_simulator.py --config config.yaml --secrets-file secrets.env --quiet
```

Stop with **Ctrl+C**: queued readings get up to 5 s to be acknowledged, then every sensor publishes `offline`.

## Test modes

| Test | Command (add `--secrets-file secrets.env`) | Expected |
| --- | --- | --- |
| Alert 1 — reading outside threshold | `--anomaly SZCZ-TEMP-01 --anomaly-count 2` | Two readings of 25.0 C, then normal values; alert fires and recovers |
| Alert 2 — sensor silent | `--silence SZCZ-AIR-01 --silence-for 300` | One reading, then 5 min without readings while status stays `online`; alert after ~120–150 s; recovers when readings resume |
| VPN interruption | Normal run; disconnect WireGuard for a planned time; reconnect | Log shows `disconnected`, `queued_offline` publishes, `connected`, `publish_resumed`; observer receives the queued readings |
| Fast functional test | `--interval 2 --count 10` | Record the overridden interval in evidence (data-design.md 4.4) |

Exclude anomaly readings from normal-operation statistics and analyse readings generated during an outage separately (`"result":"queued_offline"` in the log).

## Clock synchronisation

Delay measurements are only valid with a synchronised clock (`vpn/ntp/chrony-check.sh` on Linux). On Windows, check with `w32tm /query /status` and record the offset; default Windows time sync is less precise than chrony, which must be stated in the delay report.

## Verification

```bash
python -m unittest discover -s simulators/tests -v     # 9 offline contract tests
```

Verified on 2026-11-14 against Mosquitto 2.0.18 using `docker/mosquitto` (config, ACL, `gen-certs.sh`, `gen-passwd.sh`) with TLS:

- 8 rounds × 3 sensors delivered through the broker with contiguous `seq`; anomaly (105.0 %RH ×2) and 3 s silence behaved as specified.
- Broker stopped for 6 s: 7 readings per sensor were generated as `queued_offline` and all were delivered after the broker returned — 18/18 per sensor, no gaps, no duplicates at the observer.
- Retained status: `online` while running, `offline` after Ctrl+C, and `offline` via Last Will after `kill -9`.

Not yet run through WireGuard to the Contabo server.
