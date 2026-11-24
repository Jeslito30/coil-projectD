#!/usr/bin/env python3
"""Broker abuse checks that need two clients at once (run by docker/mosquitto/scripts/local-test.sh).

1. Client-ID takeover: an authenticated sensor connects with Telegraf's client ID. With
   use_username_as_clientid the broker renames it to the sensor's own username, so Telegraf stays up.
2. Oversized packet: a sensor publishes 1 MB to its own topic; max_packet_size must reject it and the
   observer must not receive it.

Environment: MQTT_BROKER, MQTT_PORT, MQTT_CA and the MQTT_*_PASSWORD variables from gen-passwd.sh.
"""
from __future__ import annotations

import os
import sys
import time

import paho.mqtt.client as mqtt

BROKER, PORT, CA = os.environ.get("MQTT_BROKER", "127.0.0.1"), int(os.environ["MQTT_PORT"]), os.environ["MQTT_CA"]


def client(user: str, password_var: str, client_id: str):
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=False,
                    protocol=mqtt.MQTTv311, reconnect_on_failure=False)
    c.username_pw_set(user, os.environ[password_var])
    c.tls_set(ca_certs=CA)
    c.disconnects, c.messages = [], []
    c.on_disconnect = lambda cl, u, f, rc, p=None: cl.disconnects.append(str(rc))
    c.on_message = lambda cl, u, m: cl.messages.append(len(m.payload))
    c.connect(BROKER, PORT)
    c.loop_start()
    return c


def main() -> int:
    failures = 0
    telegraf = client("telegraf", "MQTT_TELEGRAF_PASSWORD", "telegraf")
    time.sleep(1)
    intruder = client("SZCZ-TEMP-01", "MQTT_SZCZ_TEMP_01_PASSWORD", "telegraf")
    time.sleep(2)
    if telegraf.disconnects:
        print(f"FAIL  client-ID takeover: Telegraf was disconnected ({telegraf.disconnects})")
        failures += 1
    else:
        print("PASS  client-ID takeover: a sensor using Telegraf's client ID did not disconnect Telegraf")
    for c in (telegraf, intruder):
        c.loop_stop()
        c.disconnect()

    observer = client("test-observer", "MQTT_OBSERVER_PASSWORD", "test-observer")
    observer.subscribe("coil/szczecin/humidity/SZCZ-HUM-01", qos=1)
    time.sleep(0.5)
    sender = client("SZCZ-HUM-01", "MQTT_SZCZ_HUM_01_PASSWORD", "SZCZ-HUM-01")
    time.sleep(0.5)
    sender.publish("coil/szczecin/humidity/SZCZ-HUM-01", b"x" * 1_000_000, qos=0)
    time.sleep(2)
    if any(size >= 1_000_000 for size in observer.messages):
        print("FAIL  oversized packet: a 1 MB message was delivered")
        failures += 1
    elif not sender.disconnects:
        print("FAIL  oversized packet: the sender was not disconnected")
        failures += 1
    else:
        print("PASS  oversized packet: 1 MB publish rejected and the sender disconnected")
    for c in (observer, sender):
        c.loop_stop()
        c.disconnect()
    print(f"Summary: {2 - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
