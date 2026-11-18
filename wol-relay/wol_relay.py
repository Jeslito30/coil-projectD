#!/usr/bin/env python3
"""Project D Wake-on-LAN relay (optional Phase 7) implementing documents/wol-contract.md v0.1.

The relay runs inside the target LAN, receives `wake` commands over MQTT/TLS, sends the magic packet
locally and reports a single final outcome per request_id. Only `reachable` means success.

    python3 wol_relay.py --config config.yaml --secrets-file secrets.env

The protocol logic (class Relay) has no MQTT or network dependencies so every acceptance test in the
contract except the broker ACL and Last Will can be run offline (tests/test_wol_relay.py).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

SCHEMA_VERSION = 1
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$")
TARGET_RE = re.compile(r"^[A-Z]{4}-PC-\d{2}$")
COMMAND_FIELDS = {"schema_version", "request_id", "action", "target_id", "requested_by",
                  "requested_at_utc", "expires_at_utc"}
MAX_VALIDITY = timedelta(seconds=60)
RATE_LIMIT = timedelta(seconds=60)
RETENTION = timedelta(hours=24)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fmt_ts(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def parse_ts(text: str) -> datetime:
    if not isinstance(text, str) or not TS_RE.match(text):
        raise ValueError("timestamp must be ISO 8601 UTC with three fractional digits and Z")
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Target:
    target_id: str
    mac: str
    ip: str
    check: str = "icmp"            # "icmp" or "tcp:<port>"
    broadcast: str = "255.255.255.255"
    owner: str = ""                # who approved waking this computer (audit only)


def magic_packet(mac: str) -> bytes:
    if not MAC_RE.match(mac):
        raise ValueError(f"invalid MAC address {mac!r}")
    raw = bytes.fromhex(re.sub(r"[:-]", "", mac))
    return b"\xff" * 6 + raw * 16


def send_magic_packet(target: Target, port: int = 9) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic_packet(target.mac), (target.broadcast, port))


def is_reachable(target: Target, timeout: float = 1.0) -> bool:
    if target.check.startswith("tcp:"):
        try:
            with socket.create_connection((target.ip, int(target.check[4:])), timeout=timeout):
                return True
        except OSError:
            return False
    count_flag = "-n" if sys.platform.startswith("win") else "-c"
    wait = ["-w", str(int(timeout * 1000))] if sys.platform.startswith("win") else ["-W", str(max(1, int(timeout)))]
    result = subprocess.run(["ping", count_flag, "1", *wait, target.ip],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


class Store:
    """Persistent request results and per-target wake times (contract section 7, rule 6)."""

    def __init__(self, path: str):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS requests (request_id TEXT PRIMARY KEY, final_json TEXT, "
                        "created_utc TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS wakes (target_id TEXT PRIMARY KEY, last_wake_utc TEXT NOT NULL)")
        self.db.commit()

    def get(self, request_id: str) -> tuple[bool, Optional[dict]]:
        with self.lock:
            row = self.db.execute("SELECT final_json FROM requests WHERE request_id = ?", (request_id,)).fetchone()
        if row is None:
            return False, None
        return True, (json.loads(row[0]) if row[0] else None)

    def begin(self, request_id: str, now: datetime) -> bool:
        """Registers a request as in progress. False if it was already known."""
        with self.lock:
            try:
                self.db.execute("INSERT INTO requests (request_id, final_json, created_utc) VALUES (?, NULL, ?)",
                                (request_id, fmt_ts(now)))
                self.db.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def finish(self, request_id: str, result: dict) -> None:
        with self.lock:
            self.db.execute("UPDATE requests SET final_json = ? WHERE request_id = ?", (json.dumps(result), request_id))
            self.db.commit()

    def last_wake(self, target_id: str) -> Optional[datetime]:
        with self.lock:
            row = self.db.execute("SELECT last_wake_utc FROM wakes WHERE target_id = ?", (target_id,)).fetchone()
        return parse_ts(row[0]) if row else None

    def record_wake(self, target_id: str, moment: datetime) -> None:
        with self.lock:
            self.db.execute("INSERT INTO wakes (target_id, last_wake_utc) VALUES (?, ?) "
                            "ON CONFLICT(target_id) DO UPDATE SET last_wake_utc = excluded.last_wake_utc",
                            (target_id, fmt_ts(moment)))
            self.db.commit()

    def prune(self, now: datetime) -> int:
        """Drops finished requests older than the retention period. Unfinished rows from a crash are
        finalised by Relay.recover() first, so nothing in progress is lost."""
        cutoff = fmt_ts(now - RETENTION)
        with self.lock:
            cursor = self.db.execute("DELETE FROM requests WHERE created_utc < ? AND final_json IS NOT NULL", (cutoff,))
            self.db.commit()
            return cursor.rowcount

    def unfinished(self) -> list[str]:
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT request_id FROM requests WHERE final_json IS NULL")]


class Relay:
    def __init__(self, relay_id: str, targets: dict[str, Target], store: Store,
                 publish: Callable[[dict], None],
                 send_packet: Callable[[Target], None] = send_magic_packet,
                 check: Callable[[Target], bool] = is_reachable,
                 clock: Callable[[], datetime] = utc_now,
                 sleep: Callable[[float], None] = time.sleep,
                 audit: Optional[Callable[[str, dict], None]] = None,
                 packet_repeats: int = 3, packet_gap: float = 1.0,
                 poll_interval: float = 5.0, wake_timeout: float = 180.0):
        self.relay_id, self.targets, self.store = relay_id, targets, store
        self.publish_raw, self.send_packet, self.check = publish, send_packet, check
        self.clock, self.sleep = clock, sleep
        self.audit = audit or (lambda event, data: None)
        self.packet_repeats, self.packet_gap = packet_repeats, packet_gap
        self.poll_interval, self.wake_timeout = poll_interval, wake_timeout
        self.busy: set[str] = set()
        self.busy_lock = threading.Lock()

    # -- results ---------------------------------------------------------------------------------
    def result(self, request_id, target_id, state, final, reason=None, sent=None, reachable=None) -> dict:
        wake_seconds = round((reachable - sent).total_seconds(), 1) if sent and reachable else None
        target = self.targets.get(target_id) if isinstance(target_id, str) else None
        return {
            "schema_version": SCHEMA_VERSION, "request_id": request_id, "relay_id": self.relay_id,
            "target_id": target_id, "state": state, "final": final, "reason": reason,
            "packet_sent_at_utc": fmt_ts(sent) if sent else None,
            "reachable_at_utc": fmt_ts(reachable) if reachable else None,
            "wake_seconds": wake_seconds, "check": target.check if target else None,
            "timestamp_utc": fmt_ts(self.clock()),
        }

    def emit(self, message: dict) -> None:
        self.audit("result", message)
        self.publish_raw(message)

    def final(self, message: dict) -> dict:
        if message["request_id"] is not None:
            self.store.finish(message["request_id"], message)
        self.emit(message)
        return message

    # -- validation ------------------------------------------------------------------------------
    @staticmethod
    def readable_request_id(command) -> Optional[str]:
        if isinstance(command, dict) and isinstance(command.get("request_id"), str):
            try:
                if uuid.UUID(command["request_id"]).version == 4 and str(uuid.UUID(command["request_id"])) == command["request_id"].lower():
                    return command["request_id"]
            except ValueError:
                return None
        return None

    @staticmethod
    def is_malformed(command) -> bool:
        if not isinstance(command, dict) or set(command) != COMMAND_FIELDS:
            return True
        if type(command["schema_version"]) is not int or command["schema_version"] != SCHEMA_VERSION:
            return True
        if Relay.readable_request_id(command) is None:
            return True
        for key in ("action", "target_id", "requested_by"):
            if not isinstance(command[key], str) or not command[key] or len(command[key]) > 64:
                return True
        if "@" in command["requested_by"]:
            return True                      # user names only, no e-mail addresses (contract section 5)
        try:
            requested, expires = parse_ts(command["requested_at_utc"]), parse_ts(command["expires_at_utc"])
        except ValueError:
            return True
        return not (timedelta(0) < expires - requested <= MAX_VALIDITY)

    # -- main entry point ------------------------------------------------------------------------
    def handle(self, payload: bytes) -> Optional[dict]:
        """Processes one command synchronously and returns the final result (None for an in-progress duplicate)."""
        try:
            command = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            command = None
        self.audit("command", command if isinstance(command, dict) else {"raw": payload[:200].decode("utf-8", "replace")})
        request_id = self.readable_request_id(command)
        target_id = command.get("target_id") if isinstance(command, dict) and isinstance(command.get("target_id"), str) else None

        if request_id is not None:
            known, stored = self.store.get(request_id)
            if known and stored is not None:
                self.audit("duplicate_final", {"request_id": request_id})
                self.emit(stored)            # identical final result, no second execution
                return stored
            if known:
                self.audit("duplicate_in_progress", {"request_id": request_id})
                return None
            self.store.begin(request_id, self.clock())

        if self.is_malformed(command):
            return self.final(self.result(request_id, target_id, "rejected", True, "malformed"))
        if command["action"] != "wake":
            return self.final(self.result(request_id, target_id, "rejected", True, "unsupported_action"))
        if self.clock() > parse_ts(command["expires_at_utc"]):
            return self.final(self.result(request_id, target_id, "rejected", True, "expired"))
        target = self.targets.get(target_id)
        if target is None:
            return self.final(self.result(request_id, target_id, "rejected", True, "unknown_target"))
        with self.busy_lock:
            last = self.store.last_wake(target_id)
            if target_id in self.busy or (last is not None and self.clock() - last < RATE_LIMIT):
                limited = True
            else:
                limited = False
                self.busy.add(target_id)
        if limited:
            return self.final(self.result(request_id, target_id, "rejected", True, "rate_limited"))
        try:
            return self.wake(request_id, target)
        finally:
            with self.busy_lock:
                self.busy.discard(target_id)

    def wake(self, request_id: str, target: Target) -> dict:
        self.emit(self.result(request_id, target.target_id, "accepted", False))
        if self.check(target):
            return self.final(self.result(request_id, target.target_id, "already_on", True))
        sent = self.clock()
        self.store.record_wake(target.target_id, sent)
        for attempt in range(self.packet_repeats):
            self.send_packet(target)
            if attempt + 1 < self.packet_repeats:
                self.sleep(self.packet_gap)
        self.emit(self.result(request_id, target.target_id, "packet_sent", False, sent=sent))
        deadline = sent + timedelta(seconds=self.wake_timeout)
        while self.clock() < deadline:
            self.sleep(self.poll_interval)
            if self.check(target):
                return self.final(self.result(request_id, target.target_id, "reachable", True,
                                              sent=sent, reachable=self.clock()))
        return self.final(self.result(request_id, target.target_id, "unreachable_timeout", True, sent=sent))

    def recover(self) -> None:
        """A request left unfinished by a crash is finalised as rejected/expired rather than re-run:
        re-executing it could wake a computer long after the user asked."""
        for request_id in self.store.unfinished():
            self.final(self.result(request_id, None, "rejected", True, "expired"))


# -- configuration and MQTT glue ---------------------------------------------------------------------
def load_config(path: Path) -> dict:
    import yaml
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    targets = {}
    for item in config.get("targets", []):
        target = Target(**item)
        if not TARGET_RE.match(target.target_id):
            raise SystemExit(f"invalid target_id {target.target_id!r} (expected e.g. SZCZ-PC-01)")
        magic_packet(target.mac)
        if not (target.check == "icmp" or re.fullmatch(r"tcp:\d{1,5}", target.check)):
            raise SystemExit(f"{target.target_id}: check must be icmp or tcp:<port>")
        targets[target.target_id] = target
    if not targets:
        raise SystemExit("config has no targets")
    config["targets"] = targets
    return config


def read_secrets(path: Optional[Path]) -> dict[str, str]:
    import os
    values = dict(os.environ)
    if path:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def run(args) -> int:
    import paho.mqtt.client as mqtt
    config = load_config(args.config)
    relay_id = config.get("relay_id", "pl-wol-relay")
    city = config.get("city_topic", "szczecin")
    broker = config["broker"]
    cmd_topic = f"coil/{city}/wol/{relay_id}/cmd"
    result_topic = f"coil/{city}/wol/{relay_id}/result"
    status_topic = f"coil/{city}/status/{relay_id}"
    secrets = read_secrets(args.secrets_file)
    password = secrets.get("MQTT_PL_WOL_RELAY_PASSWORD") or sys.exit("MQTT_PL_WOL_RELAY_PASSWORD is not set")

    audit_path = Path(config.get("audit_log", "wol-relay-audit.jsonl"))
    audit_lock = threading.Lock()

    def audit(event: str, data: dict) -> None:
        with audit_lock, audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": fmt_ts(utc_now()), "event": event, "data": data}) + "\n")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=relay_id, clean_session=False,
                         protocol=mqtt.MQTTv311)
    client.username_pw_set(relay_id, password)
    client.tls_set(ca_certs=broker["ca_file"])
    client.will_set(status_topic, "offline", qos=1, retain=True)

    store = Store(config.get("state_db", "wol-relay-state.sqlite3"))
    relay = Relay(relay_id, config["targets"], store,
                  publish=lambda message: client.publish(result_topic, json.dumps(message, separators=(",", ":")), qos=1),
                  audit=audit, wake_timeout=float(config.get("wake_timeout_seconds", 180)))

    def on_connect(cl, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            logging.error("connect failed: %s", reason_code)
            return
        cl.publish(status_topic, "online", qos=1, retain=True)
        cl.subscribe(cmd_topic, qos=1)
        audit("connected", {"broker": broker["host"]})

    def on_message(cl, userdata, msg):
        if msg.retain:
            audit("ignored_retained_command", {"topic": msg.topic})
            return                         # contract: commands are never retained
        threading.Thread(target=relay.handle, args=(msg.payload,), daemon=True).start()

    client.on_connect, client.on_message = on_connect, on_message
    store.prune(utc_now())
    client.connect(broker["host"], int(broker.get("port", 8883)), keepalive=int(broker.get("keepalive", 30)))
    client.loop_start()
    relay.recover()
    audit("start", {"targets": sorted(config["targets"]), "relay_id": relay_id})
    try:
        while True:
            time.sleep(3600)
            store.prune(utc_now())
    except KeyboardInterrupt:
        pass
    finally:
        client.publish(status_topic, "offline", qos=1, retain=True).wait_for_publish(5)
        client.disconnect()
        client.loop_stop()
        audit("stop", {})
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Project D Wake-on-LAN relay (Phase 7)")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--secrets-file", type=Path)
    parser.add_argument("--send-test-packet", metavar="TARGET_ID",
                        help="send one magic packet to an allowlisted target and exit (no MQTT)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.send_test_packet:
        target = load_config(args.config)["targets"].get(args.send_test_packet)
        if target is None:
            print("target not in allowlist", file=sys.stderr)
            return 2
        send_magic_packet(target)
        print(f"magic packet sent to {target.target_id} via {target.broadcast}:9")
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
