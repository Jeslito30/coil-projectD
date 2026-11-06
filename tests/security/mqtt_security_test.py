#!/usr/bin/env python3
"""Run seven Project D MQTT security checks and save timestamped JSON results.

The own-topic check publishes ONE real reading to the production topic. Its value
is the midpoint of the sensor's normal range (no threshold alert) and the UTC test
window is written to the result file, so delay and sequence analysis can exclude it
(see check_seq.py --exclude-window).
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import ssl
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("Missing dependency: python -m pip install -r tests/requirements.txt", file=sys.stderr)
    raise SystemExit(2)


def new_client(client_id: str):
    # Paho 2.x uses callback API v2; the callbacks below accept both v1 and v2 signatures.
    try:
        return mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
    except AttributeError:
        return mqtt.Client(client_id=client_id, clean_session=True, protocol=mqtt.MQTTv311)


# Midpoint of each sensor's normal range (data-design.md section 5), so the test
# reading never crosses an alert threshold (section 6.1).
NORMAL_MIDPOINT = {
    ("mati", "temperature"): 28.5,
    ("mati", "humidity"): 80.0,
    ("mati", "airquality"): 20.0,
    ("szczecin", "temperature"): 3.5,
    ("szczecin", "humidity"): 84.0,
    ("szczecin", "airquality"): 32.5,
}
UNITS = {"temperature": "C", "humidity": "%RH", "airquality": "ug/m3"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class MqttConnection:
    def __init__(self, args, client_id: str, username=None, password=None, tls=True, port=None,
                 on_message=None, ca_override="__project__"):
        self.args = args
        self.client = new_client(client_id)
        self.event = threading.Event()
        self.rc = None
        self.error = None
        self.tls_verify_failed = False
        self.client.on_message = on_message
        if username is not None:
            self.client.username_pw_set(username, password)
        if tls:
            # ca_override=None means "system trust store only", which must NOT trust the project CA.
            ca_file = args.ca if ca_override == "__project__" else ca_override
            self.client.tls_set(ca_certs=ca_file, cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
            self.client.tls_insecure_set(False)
        self.port = port or (args.tls_port if tls else args.plaintext_port)

        def on_connect(client, userdata, flags, reason_code, properties=None):
            # v1 passes an int rc, v2 a ReasonCode; int() works for both.
            self.rc = int(reason_code.value if hasattr(reason_code, "value") else reason_code)
            self.event.set()

        self.client.on_connect = on_connect

    def connect(self) -> bool:
        try:
            self.client.connect(self.args.broker, self.port, keepalive=20)
            self.client.loop_start()
            if not self.event.wait(self.args.timeout):
                self.error = "connection timeout"
                return False
            if self.rc != 0:
                self.error = f"broker CONNACK rc={self.rc}"
                return False
            return True
        except ssl.SSLCertVerificationError as exc:
            self.tls_verify_failed = True
            self.error = f"{type(exc).__name__}: {exc}"
            return False
        except (OSError, ssl.SSLError, ValueError) as exc:
            if "certificate verify failed" in str(exc).lower():
                self.tls_verify_failed = True
            self.error = f"{type(exc).__name__}: {exc}"
            return False

    def close(self):
        try:
            self.client.disconnect()
        except Exception:
            pass
        self.client.loop_stop()


def payload_for(topic: str, token: str) -> str:
    _, city, sensor_type, sensor_id = topic.split("/")
    return json.dumps({
        "schema_version": 1,
        "sensor_id": sensor_id,
        "city": "Mati" if city == "mati" else "Szczecin",
        "sensor_type": sensor_type,
        "timestamp_utc": utc_now(),
        # seq 0 looks like a simulator restart; exclude the test window in check_seq.py.
        "seq": 0,
        "value": NORMAL_MIDPOINT[(city, sensor_type)],
        "unit": UNITS[sensor_type],
        "simulated": True,
        # Extra field is ignored by Telegraf and identifies the test message.
        "test_token": token,
    }, separators=(",", ":"))


def wait_for_token(messages: queue.Queue, token: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            payload = messages.get(timeout=max(0.05, deadline - time.monotonic()))
        except queue.Empty:
            break
        try:
            if json.loads(payload).get("test_token") == token:
                return True
        except (json.JSONDecodeError, AttributeError):
            continue
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker", default=os.environ.get("MQTT_BROKER", "10.66.0.1"))
    parser.add_argument("--tls-port", type=int, default=8883)
    parser.add_argument("--plaintext-port", type=int, default=1883)
    parser.add_argument("--ca", default=os.environ.get("MQTT_CA"), help="Project CA certificate")
    parser.add_argument("--username", default=os.environ.get("MQTT_TEST_USERNAME"))
    parser.add_argument("--own-topic", required=True)
    parser.add_argument("--other-topic", required=True)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    password = os.environ.get("MQTT_TEST_PASSWORD")
    # Dedicated read-only account from the ACL; never reuse Telegraf's production password on a laptop.
    observer_user = os.environ.get("MQTT_OBSERVER_USERNAME", "test-observer")
    observer_password = os.environ.get("MQTT_OBSERVER_PASSWORD")
    if not args.ca or not args.username or password is None or not observer_password:
        parser.error("MQTT_CA, MQTT_TEST_USERNAME, MQTT_TEST_PASSWORD and MQTT_OBSERVER_PASSWORD are required")
    if not os.path.isfile(args.ca):
        parser.error(f"CA file not found: {args.ca}")
    if args.own_topic == args.other_topic:
        parser.error("--other-topic must differ from --own-topic")
    for topic in (args.own_topic, args.other_topic):
        parts = topic.split("/")
        if len(parts) != 4 or parts[0] != "coil" or parts[1] not in ("mati", "szczecin") or parts[2] not in ("temperature", "humidity", "airquality"):
            parser.error(f"Not a canonical Project D reading topic: {topic}")

    run_id = uuid.uuid4().hex[:10]
    results = []
    connections = []
    window_start = utc_now()

    def record(name: str, passed: bool, detail: str):
        results.append({"test": name, "result": "PASS" if passed else "FAIL", "detail": detail})
        print(f"{'PASS' if passed else 'FAIL'}  {name}: {detail}")

    def connection_test(name: str, username, secret, tls=True, port=None,
                        expect_success=True, rejection_mode="any"):
        connection = MqttConnection(
            args, f"projectd-{name}-{run_id}", username, secret, tls=tls, port=port,
        )
        connections.append(connection)
        connected = connection.connect()
        detail = "connected" if connected else (connection.error or "connection rejected")
        if expect_success:
            passed = connected
            detail = "valid credentials accepted" if passed else detail
        elif connected:
            passed = False
            detail = "unexpected connection success"
        elif rejection_mode == "connack":
            # A timeout, DNS failure or TLS error is not proof of authentication policy.
            passed = connection.rc is not None and connection.rc != 0
            detail = (f"broker rejected authentication with CONNACK rc={connection.rc}"
                      if passed else f"inconclusive network/TLS failure: {detail}")
        else:
            passed = True
            detail = f"plaintext connection failed as required: {detail}"
        record(name, passed, detail)
        connection.close()
        connections.remove(connection)
        return connected

    try:
        connection_test("correct_credentials", args.username, password, expect_success=True)
        connection_test("wrong_password", args.username, password + "-invalid",
                        expect_success=False, rejection_mode="connack")
        connection_test("missing_credentials", None, None,
                        expect_success=False, rejection_mode="connack")

        messages = queue.Queue()

        def on_message(client, userdata, message):
            messages.put(message.payload.decode("utf-8", errors="replace"))

        observer = MqttConnection(
            args, f"projectd-observer-{run_id}", observer_user, observer_password,
            on_message=on_message,
        )
        connections.append(observer)
        subscribed = threading.Event()
        observer.client.on_subscribe = lambda client, userdata, mid, *rest: subscribed.set()
        if not observer.connect():
            record("own_topic_publish", False, f"observer connection failed: {observer.error}")
            record("other_topic_rejected", False, "observer unavailable; cannot verify broker delivery")
        else:
            observer.client.subscribe([(args.own_topic, 1), (args.other_topic, 1)], qos=1)
            if not subscribed.wait(args.timeout):
                record("own_topic_publish", False, "observer SUBACK timeout")
                record("other_topic_rejected", False, "observer subscriptions not confirmed")
            else:
                actor = MqttConnection(args, f"projectd-actor-{run_id}", args.username, password)
                connections.append(actor)
                if not actor.connect():
                    record("own_topic_publish", False, f"actor connection failed: {actor.error}")
                    record("other_topic_rejected", False, "actor unavailable")
                else:
                    own_token = uuid.uuid4().hex
                    try:
                        own_info = actor.client.publish(
                            args.own_topic, payload_for(args.own_topic, own_token), qos=1, retain=False,
                        )
                        own_info.wait_for_publish(timeout=args.timeout)
                        own_seen = wait_for_token(messages, own_token, args.timeout)
                        own_detail = ("observer received the unique valid reading" if own_seen
                                      else "observer did not receive the own-topic reading")
                    except (RuntimeError, OSError, ValueError) as exc:
                        own_seen = False
                        own_detail = f"own-topic publish error: {exc}"
                    record("own_topic_publish", own_seen, own_detail)

                    if not actor.client.is_connected():
                        record("other_topic_rejected", False, "actor disconnected before the ACL test")
                    else:
                        other_token = uuid.uuid4().hex
                        try:
                            actor.client.publish(
                                args.other_topic, payload_for(args.other_topic, other_token), qos=1, retain=False,
                            )
                            other_seen = wait_for_token(messages, other_token, args.timeout)
                            other_detail = ("no cross-topic reading observed; ACL denial confirmed at delivery"
                                            if not other_seen else "ACL failure: observer received the other-sensor topic")
                            record("other_topic_rejected", not other_seen, other_detail)
                        except (RuntimeError, OSError, ValueError) as exc:
                            record("other_topic_rejected", False, f"other-topic test error: {exc}")
                actor.close()
                connections.remove(actor)
        observer.close()
        connections.remove(observer)

        connection_test("plaintext_rejected", args.username, password, tls=False,
                        port=args.plaintext_port, expect_success=False)

        # Untrusted CA: trust only the system store, which does not contain the project CA.
        # Only a certificate-verification failure counts; a timeout is inconclusive.
        untrusted = MqttConnection(
            args, f"projectd-untrusted-ca-{run_id}", args.username, password, ca_override=None,
        )
        connections.append(untrusted)
        connected = untrusted.connect()
        if connected:
            record("untrusted_ca_rejected", False, "unexpected success: server certificate trusted without the project CA")
        elif untrusted.tls_verify_failed:
            record("untrusted_ca_rejected", True, f"TLS verification failed as required: {untrusted.error}")
        else:
            record("untrusted_ca_rejected", False, f"inconclusive (not a certificate error): {untrusted.error}")
        untrusted.close()
        connections.remove(untrusted)
    finally:
        for connection in connections:
            connection.close()

    window_end = utc_now()
    summary = {
        "run_utc": window_end,
        "broker": args.broker,
        "test_window_utc": {"start": window_start, "end": window_end},
        "tests": results,
        "passed": sum(item["result"] == "PASS" for item in results),
        "failed": sum(item["result"] == "FAIL" for item in results),
        "notes": [
            "Own/other-topic tests use a read-only observer and unique payload tokens.",
            "One in-range test reading with seq 0 was published to the own topic inside test_window_utc; "
            "exclude this window in check_seq.py and delay reports.",
            "No passwords, tokens or CA private material are included in this result.",
        ],
    }
    output = args.output or Path(__file__).parent / "results" / f"mqtt-security-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Summary: {summary['passed']} passed, {summary['failed']} failed; wrote {output}")
    print(f"Test window (exclude from sequence/delay analysis): {window_start},{window_end}")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
