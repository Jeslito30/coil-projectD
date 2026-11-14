#!/usr/bin/env python3
"""Project D sensor simulator (Mati or Szczecin), implementing documents/data-design.md v0.2.

One MQTT client per sensor (own username, client ID, Last Will and ACL), MQTT 3.1.1 with
clean session false, QoS 1 readings (retain false), retained online/offline status, TLS with
the project CA, millisecond UTC timestamps, per-run `seq`, bounded in-memory offline queue
and a JSON-lines event log for interruption and delay analysis.

Run `--dry-run` to print payloads without a broker. See simulators/README.md.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import signal
import ssl
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

# --- Contract tables (data-design.md sections 1.2, 1.3, 5 and 6.1) -------------------------
CITY_TOPIC = {"Mati": "mati", "Szczecin": "szczecin"}
CITY_PREFIX = {"Mati": "MATI", "Szczecin": "SZCZ"}
TYPE_CODE = {"temperature": "TEMP", "humidity": "HUM", "airquality": "AIR"}
UNITS = {"temperature": "C", "humidity": "%RH", "airquality": "ug/m3"}

# (normal_min, normal_max, max_step)
NORMAL = {
    ("Mati", "temperature"): (24.0, 33.0, 0.3),
    ("Mati", "humidity"): (65.0, 95.0, 1.0),
    ("Mati", "airquality"): (5.0, 35.0, 1.5),
    ("Szczecin", "temperature"): (-3.0, 10.0, 0.3),
    ("Szczecin", "humidity"): (70.0, 98.0, 1.0),
    ("Szczecin", "airquality"): (5.0, 60.0, 2.0),
}
# (alert_below or None, alert_above)
THRESHOLDS = {
    ("Mati", "temperature"): (20.0, 38.0),
    ("Mati", "humidity"): (40.0, 100.0),
    ("Mati", "airquality"): (None, 55.5),
    ("Szczecin", "temperature"): (-15.0, 20.0),
    ("Szczecin", "humidity"): (40.0, 100.0),
    ("Szczecin", "airquality"): (None, 75.0),
}
# Anomaly values cross the upper threshold by a clear margin (data-design.md 6.1).
ANOMALY_MARGIN = {"temperature": 5.0, "humidity": 5.0, "airquality": 20.0}

SENSOR_ID_RE = re.compile(r"^(MATI|SZCZ)-(TEMP|HUM|AIR)-(0[1-9]|[1-9][0-9])$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
FIELDS = {"schema_version", "sensor_id", "city", "sensor_type", "timestamp_utc", "seq", "value", "unit", "simulated"}


def utc_now_ms() -> str:
    """ISO 8601 UTC with exactly three fractional digits and a Z suffix."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def reading_topic(city: str, sensor_type: str, sensor_id: str) -> str:
    return f"coil/{CITY_TOPIC[city]}/{sensor_type}/{sensor_id}"


def status_topic(city: str, sensor_id: str) -> str:
    return f"coil/{CITY_TOPIC[city]}/status/{sensor_id}"


def password_env_name(sensor_id: str) -> str:
    """Matches docker/mosquitto/scripts/gen-passwd.sh, e.g. MQTT_SZCZ_TEMP_01_PASSWORD."""
    return "MQTT_" + sensor_id.upper().replace("-", "_") + "_PASSWORD"


def validate_reading(topic: str, reading: dict) -> list[str]:
    """Field, type and cross-field checks from data-design.md section 3.1. Returns a list of errors."""
    errors: list[str] = []
    if set(reading) != FIELDS:
        missing, extra = FIELDS - set(reading), set(reading) - FIELDS
        if missing:
            errors.append(f"missing fields: {sorted(missing)}")
        if extra:
            errors.append(f"unexpected fields: {sorted(extra)}")
        return errors
    if type(reading["schema_version"]) is not int or reading["schema_version"] != SCHEMA_VERSION:
        errors.append("schema_version must be integer 1")
    sensor_id, city, sensor_type = reading["sensor_id"], reading["city"], reading["sensor_type"]
    if not isinstance(sensor_id, str) or not SENSOR_ID_RE.match(sensor_id):
        errors.append(f"invalid sensor_id: {sensor_id!r}")
    if city not in CITY_TOPIC:
        errors.append(f"invalid city: {city!r}")
    if sensor_type not in UNITS:
        errors.append(f"invalid sensor_type: {sensor_type!r}")
    if errors:
        return errors
    if not sensor_id.startswith(CITY_PREFIX[city] + "-" + TYPE_CODE[sensor_type] + "-"):
        errors.append("sensor_id does not match city/sensor_type")
    if topic != reading_topic(city, sensor_type, sensor_id):
        errors.append(f"topic {topic!r} does not match payload")
    if not isinstance(reading["timestamp_utc"], str) or not TIMESTAMP_RE.match(reading["timestamp_utc"]):
        errors.append("timestamp_utc must look like 2026-11-10T08:30:00.123Z")
    if type(reading["seq"]) is not int or reading["seq"] < 0:
        errors.append("seq must be a non-negative integer")
    value = reading["value"]
    if type(value) is not float or not math.isfinite(value):
        errors.append("value must be a finite float (serialised with a decimal point)")
    if reading["unit"] != UNITS[sensor_type]:
        errors.append(f"unit must be {UNITS[sensor_type]!r}")
    if reading["simulated"] is not True:
        errors.append("simulated must be true")
    return errors


def anomaly_value(city: str, sensor_type: str) -> float:
    _, above = THRESHOLDS[(city, sensor_type)]
    return round(above + ANOMALY_MARGIN[sensor_type], 1)


class RandomWalk:
    """Slow random walk inside the normal range; |step| never exceeds max_step (data-design.md 5)."""

    def __init__(self, city: str, sensor_type: str, rng: random.Random):
        self.low, self.high, self.max_step = NORMAL[(city, sensor_type)]
        self.rng = rng
        middle = (self.low + self.high) / 2
        spread = (self.high - self.low) / 4
        self.value = round(rng.uniform(middle - spread, middle + spread), 1)

    def next(self) -> float:
        tenths = int(round(self.max_step * 10))
        delta = self.rng.randint(-tenths, tenths) / 10
        candidate = round(self.value + delta, 1)
        if candidate < self.low or candidate > self.high:
            # Reflect at the boundary so the step size limit still holds.
            candidate = round(self.value - delta, 1)
        self.value = candidate
        return candidate


def build_reading(city: str, sensor_type: str, sensor_id: str, seq: int, value: float) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "sensor_id": sensor_id,
        "city": city,
        "sensor_type": sensor_type,
        "timestamp_utc": utc_now_ms(),
        "seq": seq,
        "value": float(value),
        "unit": UNITS[sensor_type],
        "simulated": True,
    }


class EventLog:
    """Thread-safe JSON-lines log (also echoed to stdout in short form)."""

    def __init__(self, path: Path | None, quiet: bool):
        self.lock = threading.Lock()
        self.file = path.open("a", encoding="utf-8") if path else None
        self.quiet = quiet

    def write(self, event: str, **data) -> None:
        record = {"time_utc": utc_now_ms(), "event": event, **data}
        line = json.dumps(record, separators=(",", ":"))
        with self.lock:
            if self.file:
                self.file.write(line + "\n")
                self.file.flush()
            if not self.quiet or event not in {"publish", "puback"}:
                print(line, flush=True)

    def close(self) -> None:
        if self.file:
            self.file.close()


@dataclass
class SensorState:
    sensor_id: str
    sensor_type: str
    walk: RandomWalk
    seq: int = 0
    pending: int = 0                      # published locally, PUBACK not yet received (queued + in flight)
    connected: bool = False
    awaiting_resume: bool = False         # set on (re)connect; cleared by the first PUBACK afterwards
    anomalies_left: int = 0
    silence_after: int | None = None      # number of readings before silence starts
    silence_until: float | None = None    # monotonic time when silence ends (None = until stop)
    silenced: bool = False
    client: object = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    pending_mids: set = field(default_factory=set)
    early_acks: set = field(default_factory=set)  # reading PUBACKs that arrived before the mid was registered
    status_mids: set = field(default_factory=set)  # mids of online/offline status publishes


def load_config(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise SystemExit("PyYAML is required for YAML configs: pip install -r simulators/requirements.txt") from exc
        config = yaml.safe_load(text)
    else:
        config = json.loads(text)
    if not isinstance(config, dict):
        raise SystemExit("Config must be a mapping")
    return config


def load_secrets_file(path: Path) -> None:
    """Load KEY=VALUE lines (format of secrets/mqtt-passwords.env) into the environment, without overriding."""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project D sensor simulator (data-design.md v0.2)")
    parser.add_argument("--config", required=True, type=Path, help="YAML or JSON config (see szczecin/config.example.yaml)")
    parser.add_argument("--secrets-file", type=Path, help="Optional KEY=VALUE file with MQTT_<SENSOR>_PASSWORD entries")
    parser.add_argument("--interval", type=float, help="Override the 30 s interval (testing only; recorded in the log)")
    parser.add_argument("--anomaly", action="append", default=[], metavar="SENSOR_ID",
                        help="Publish readings above the alert threshold for this sensor (Alert 1 test)")
    parser.add_argument("--anomaly-count", type=int, default=1, help="Number of anomaly readings per --anomaly sensor")
    parser.add_argument("--silence", action="append", default=[], metavar="SENSOR_ID",
                        help="Stop readings for this sensor but keep its MQTT connection (Alert 2 test)")
    parser.add_argument("--silence-after", type=int, default=1,
                        help="Readings to publish before silence starts (default 1, so the series exists)")
    parser.add_argument("--silence-for", type=float, default=0,
                        help="Seconds of silence before resuming; 0 = silent until the simulator stops")
    parser.add_argument("--count", type=int, default=0, help="Stop after this many rounds (0 = run until Ctrl+C)")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads only; no broker connection")
    parser.add_argument("--log-file", type=Path, help="JSON-lines event log (default from config or simulator-<city>.jsonl)")
    parser.add_argument("--quiet", action="store_true", help="Do not echo every publish/puback to stdout")
    parser.add_argument("--seed", type=int, help="Random seed (reproducible tests)")
    return parser.parse_args(argv)


class Simulator:
    def __init__(self, args: argparse.Namespace, config: dict):
        self.args = args
        self.config = config
        self.city = config.get("city")
        if self.city not in CITY_TOPIC:
            raise SystemExit(f"config 'city' must be one of {sorted(CITY_TOPIC)}")
        broker = config.get("broker") or {}
        self.host = broker.get("host", "10.66.0.1")
        self.port = int(broker.get("port", 8883))
        self.ca_file = broker.get("ca_file")
        self.keepalive = int(broker.get("keepalive", 30))
        self.queue_limit = int(config.get("queue_limit", 2000))
        self.interval = float(args.interval or config.get("interval_seconds", 30))
        if self.interval <= 0:
            raise SystemExit("interval must be positive")
        self.rng = random.Random(args.seed)
        self.stop_event = threading.Event()
        log_path = args.log_file or Path(config.get("log_file") or f"simulator-{CITY_TOPIC[self.city]}.jsonl")
        self.log = EventLog(None if args.dry_run and not args.log_file else log_path, args.quiet)
        self.sensors = self._build_sensors(config.get("sensors") or [])

    def _build_sensors(self, entries: list) -> list[SensorState]:
        if not entries:
            raise SystemExit("config 'sensors' is empty")
        sensors, seen = [], set()
        for entry in entries:
            sensor_id, sensor_type = entry.get("sensor_id"), entry.get("sensor_type")
            if sensor_type not in UNITS or not isinstance(sensor_id, str) or not SENSOR_ID_RE.match(sensor_id):
                raise SystemExit(f"invalid sensor entry: {entry}")
            if not sensor_id.startswith(CITY_PREFIX[self.city] + "-" + TYPE_CODE[sensor_type] + "-"):
                raise SystemExit(f"{sensor_id} does not belong to {self.city}/{sensor_type}")
            if sensor_id in seen:
                raise SystemExit(f"duplicate sensor {sensor_id}")
            seen.add(sensor_id)
            state = SensorState(sensor_id, sensor_type, RandomWalk(self.city, sensor_type, self.rng))
            if sensor_id in self.args.anomaly:
                state.anomalies_left = max(1, self.args.anomaly_count)
            if sensor_id in self.args.silence:
                state.silence_after = max(0, self.args.silence_after)
            sensors.append(state)
        unknown = (set(self.args.anomaly) | set(self.args.silence)) - seen
        if unknown:
            raise SystemExit(f"--anomaly/--silence refer to sensors not in config: {sorted(unknown)}")
        return sensors

    # --- MQTT ------------------------------------------------------------------------------
    def _make_client(self, state: SensorState):
        import paho.mqtt.client as mqtt

        password = os.environ.get(password_env_name(state.sensor_id))
        if not password:
            raise SystemExit(f"Missing password: set {password_env_name(state.sensor_id)} (or use --secrets-file)")
        if not self.ca_file or not Path(self.ca_file).is_file():
            raise SystemExit(f"CA file not found: {self.ca_file!r} (broker.ca_file in config)")

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=state.sensor_id,
            clean_session=False,
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(state.sensor_id, password)
        # Verify the broker certificate against the project CA; never disable verification.
        client.tls_set(ca_certs=self.ca_file, cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        client.tls_insecure_set(False)
        client.will_set(status_topic(self.city, state.sensor_id), "offline", qos=1, retain=True)
        client.max_queued_messages_set(self.queue_limit)
        client.max_inflight_messages_set(20)
        client.reconnect_delay_set(min_delay=1, max_delay=30)

        def on_connect(cl, userdata, flags, reason_code, properties=None):
            code = reason_code.value if hasattr(reason_code, "value") else int(reason_code)
            if code == 0:
                state.connected = True
                state.awaiting_resume = True
                info = cl.publish(status_topic(self.city, state.sensor_id), "online", qos=1, retain=True)
                with state.lock:
                    state.status_mids.add(info.mid)
                self.log.write("connected", sensor_id=state.sensor_id,
                               session_present=bool(getattr(flags, "session_present", False)),
                               pending=state.pending)
            else:
                self.log.write("connect_refused", sensor_id=state.sensor_id, reason=str(reason_code), code=code)

        def on_disconnect(cl, userdata, flags, reason_code, properties=None):
            state.connected = False
            self.log.write("disconnected", sensor_id=state.sensor_id, reason=str(reason_code), pending=state.pending)

        def on_publish(cl, userdata, mid, reason_code=None, properties=None):
            with state.lock:
                if mid in state.status_mids:
                    state.status_mids.discard(mid)
                    return
                if mid in state.pending_mids:
                    state.pending_mids.discard(mid)
                    state.pending = max(0, state.pending - 1)
                    is_reading = True
                else:
                    # A reading acknowledged before publish_round registered its mid.
                    state.early_acks.add(mid)
                    is_reading = False
                resumed = is_reading and state.awaiting_resume
                if resumed:
                    state.awaiting_resume = False
            if resumed:
                # Healthy only once a reading is actually acknowledged after (re)connect.
                self.log.write("publish_resumed", sensor_id=state.sensor_id, pending=state.pending)
            if is_reading:
                self.log.write("puback", sensor_id=state.sensor_id, mid=mid, pending=state.pending)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_publish = on_publish
        return client

    def connect_all(self) -> None:
        for state in self.sensors:
            state.client = self._make_client(state)
            state.client.connect_async(self.host, self.port, keepalive=self.keepalive)
            state.client.loop_start()

    # --- Publishing ---------------------------------------------------------------------------
    def _should_skip(self, state: SensorState, now: float) -> bool:
        if state.silence_after is None:
            return False
        if not state.silenced and state.seq >= state.silence_after and state.silence_until is None:
            state.silenced = True
            state.silence_until = now + self.args.silence_for if self.args.silence_for > 0 else math.inf
            self.log.write("silence_start", sensor_id=state.sensor_id,
                           planned_seconds=self.args.silence_for or None, connected=state.connected)
        if state.silenced and now >= state.silence_until:
            state.silenced = False
            state.silence_after = None
            self.log.write("silence_end", sensor_id=state.sensor_id)
        return state.silenced

    def publish_round(self) -> None:
        import paho.mqtt.client as mqtt

        now = time.monotonic()
        for state in self.sensors:
            if self._should_skip(state, now):
                continue
            anomaly = state.anomalies_left > 0
            # Anomaly readings do not move the random-walk state, so normal values resume smoothly.
            value = anomaly_value(self.city, state.sensor_type) if anomaly else state.walk.next()
            reading = build_reading(self.city, state.sensor_type, state.sensor_id, state.seq, value)
            topic = reading_topic(self.city, state.sensor_type, state.sensor_id)
            errors = validate_reading(topic, reading)
            if errors:
                # Never publish data that breaks the contract.
                self.log.write("invalid_reading", sensor_id=state.sensor_id, errors=errors)
                state.seq += 1
                continue
            payload = json.dumps(reading, separators=(",", ":"))
            if self.args.dry_run:
                print(f"{topic} {payload}", flush=True)
                rc_name, queued = "dry_run", None
            else:
                info = state.client.publish(topic, payload, qos=1, retain=False)
                if info.rc == mqtt.MQTT_ERR_QUEUE_SIZE:
                    rc_name, queued = "dropped_queue_full", False
                    self.log.write("queue_overflow", sensor_id=state.sensor_id, seq=state.seq,
                                   queue_limit=self.queue_limit, pending=state.pending)
                else:
                    # MQTT_ERR_NO_CONN still queues QoS 1 messages in paho until reconnect.
                    rc_name = "sent" if info.rc == mqtt.MQTT_ERR_SUCCESS else "queued_offline"
                    queued = True
                    with state.lock:
                        if info.mid in state.early_acks:
                            # Acknowledged before we got here: count it as delivered immediately.
                            state.early_acks.discard(info.mid)
                            state.awaiting_resume = False
                        else:
                            state.pending_mids.add(info.mid)
                            state.pending += 1
            self.log.write("publish", sensor_id=state.sensor_id, seq=state.seq, value=value,
                           timestamp_utc=reading["timestamp_utc"], anomaly=anomaly, result=rc_name,
                           connected=state.connected, pending=state.pending, queue_limit=self.queue_limit,
                           accepted=queued)
            if anomaly:
                state.anomalies_left -= 1
            state.seq += 1

    def run(self) -> int:
        self.log.write("start", city=self.city, sensors=[s.sensor_id for s in self.sensors],
                       broker=None if self.args.dry_run else f"{self.host}:{self.port}",
                       interval_seconds=self.interval, interval_overridden=self.args.interval is not None,
                       queue_limit=self.queue_limit, queue_persistence="memory-only (lost on process restart)",
                       anomaly=self.args.anomaly, silence=self.args.silence, dry_run=self.args.dry_run)
        if not self.args.dry_run:
            self.connect_all()
        rounds = 0
        next_tick = time.monotonic()
        try:
            while not self.stop_event.is_set():
                self.publish_round()
                rounds += 1
                if self.args.count and rounds >= self.args.count:
                    break
                next_tick += self.interval
                # Fixed-rate schedule without drift; if we fall behind, continue from now.
                delay = next_tick - time.monotonic()
                if delay < 0:
                    next_tick = time.monotonic()
                    delay = 0
                self.stop_event.wait(delay)
        finally:
            self.shutdown()
        return 0

    def shutdown(self) -> None:
        if not self.args.dry_run:
            deadline = time.monotonic() + 5
            while any(s.pending and s.connected for s in self.sensors) and time.monotonic() < deadline:
                time.sleep(0.1)  # give queued readings a moment to be acknowledged
            for state in self.sensors:
                if state.client is None:
                    continue
                if state.connected:
                    info = state.client.publish(status_topic(self.city, state.sensor_id), "offline", qos=1, retain=True)
                    try:
                        info.wait_for_publish(timeout=3)
                    except (RuntimeError, ValueError):
                        pass
                state.client.disconnect()
                state.client.loop_stop()
        self.log.write("stop", unacknowledged={s.sensor_id: s.pending for s in self.sensors},
                       last_seq={s.sensor_id: s.seq - 1 for s in self.sensors})
        self.log.close()


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.secrets_file:
        load_secrets_file(args.secrets_file)
    simulator = Simulator(args, load_config(args.config))

    def handle_stop(signum, frame):
        simulator.stop_event.set()

    signal.signal(signal.SIGINT, handle_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_stop)
    return simulator.run()


if __name__ == "__main__":
    sys.exit(main())
