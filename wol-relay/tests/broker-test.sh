#!/usr/bin/env bash
# End-to-end test of wol_relay.py against a throw-away local Mosquitto using the project's TLS setup,
# the core ACL plus the Phase 7 entries from documents/wol-contract.md section 4.
# Covers contract tests 1, 5, 8 and 9 over a real broker. A fake "computer" listens for the magic packet
# on 127.0.0.1:9 and only then opens its TCP check port, so `reachable` really depends on the packet.
# Needs: root (UDP port 9), mosquitto, mosquitto_pub/sub, openssl, python3 with paho-mqtt and PyYAML.
set -Eeuo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -- "$HERE/../.." && pwd)"
MQ="$REPO/docker/mosquitto"
WORK="$(mktemp -d)"
PORT="${MQTT_TEST_PORT:-18884}"
CHECK_PORT=39389
PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done; rm -rf "$WORK"; }
trap cleanup EXIT

mkdir -p "$WORK/config" "$WORK/certs" "$WORK/data"
"$MQ/scripts/gen-certs.sh" "$WORK/certs" >/dev/null
"$MQ/scripts/gen-passwd.sh" "$WORK/config" "$WORK/secrets.env" >/dev/null
RELAY_PW="$(openssl rand -hex 16)"; BACKEND_PW="$(openssl rand -hex 16)"
mosquitto_passwd -b "$WORK/config/passwd" pl-wol-relay "$RELAY_PW"
mosquitto_passwd -b "$WORK/config/passwd" backend "$BACKEND_PW"
cp "$MQ/config/acl" "$WORK/config/acl"
# Phase 7 entries exactly as in documents/wol-contract.md section 4.
sed -n '/^# Phase 7 - Wake-on-LAN relay (Szczecin)/,/^```/p' "$REPO/documents/wol-contract.md" | grep -v '^```' >> "$WORK/config/acl"
grep -q "user pl-wol-relay" "$WORK/config/acl" || { echo "could not extract ACL from the contract"; exit 1; }
chmod 644 "$WORK/certs/"* "$WORK/config/"*
sed -e "s#/mosquitto/config#$WORK/config#g" -e "s#/mosquitto/certs#$WORK/certs#g" \
    -e "s#/mosquitto/data#$WORK/data#g" -e "s#^listener 8883#listener $PORT 127.0.0.1#" \
    "$MQ/config/mosquitto.conf" > "$WORK/mosquitto.conf"
[[ "$(id -u)" == 0 ]] && echo "user root" >> "$WORK/mosquitto.conf"
mosquitto -c "$WORK/mosquitto.conf" > "$WORK/broker.log" 2>&1 & PIDS+=($!)
for _ in $(seq 1 30); do (echo > "/dev/tcp/127.0.0.1/$PORT") 2>/dev/null && break; sleep 0.3; done

# Fake computer: boots 2 s after a valid magic packet for its MAC.
python3 - "$CHECK_PORT" > "$WORK/computer.log" 2>&1 <<'PY' & PIDS+=($!)
import socket, sys, time
mac = bytes.fromhex("aabbccddee01")
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); udp.bind(("127.0.0.1", 9))
while True:
    data, _ = udp.recvfrom(1024)
    if data == b"\xff" * 6 + mac * 16:
        print("magic packet received", flush=True); break
time.sleep(2)
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", int(sys.argv[1]))); srv.listen(5); print("computer on", flush=True)
while True:
    conn, _ = srv.accept(); conn.close()
PY

cat > "$WORK/relay.yaml" <<EOF
relay_id: pl-wol-relay
city_topic: szczecin
broker: {host: 127.0.0.1, port: $PORT, ca_file: $WORK/certs/ca.crt, keepalive: 10}
state_db: $WORK/state.sqlite3
audit_log: $WORK/audit.jsonl
wake_timeout_seconds: 30
targets:
  - {target_id: SZCZ-PC-01, mac: "AA:BB:CC:DD:EE:01", ip: 127.0.0.1, check: "tcp:$CHECK_PORT", broadcast: 127.0.0.1, owner: test}
EOF
MQTT_PL_WOL_RELAY_PASSWORD="$RELAY_PW" python3 "$REPO/wol-relay/wol_relay.py" --config "$WORK/relay.yaml" > "$WORK/relay.log" 2>&1 & RELAY_PID=$!; PIDS+=($RELAY_PID)
sleep 1.5

CA="$WORK/certs/ca.crt"
sub() { mosquitto_sub -h 127.0.0.1 -p "$PORT" --cafile "$CA" -u backend -P "$BACKEND_PW" -q 1 "$@"; }
pub() { mosquitto_pub -h 127.0.0.1 -p "$PORT" --cafile "$CA" -q 1 "$@"; }
# The broker forces client ID = username, so the backend must publish and subscribe on ONE connection
# (two concurrent "backend" connections would take each other over). This helper does both.
backend_cmd() {  # backend_cmd PAYLOAD COUNT TIMEOUT_SECONDS > results
  python3 - "$PORT" "$CA" "$BACKEND_PW" "$1" "$2" "$3" <<'PY'
import sys, time, paho.mqtt.client as mqtt
port, ca, pw, payload, count, timeout = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), float(sys.argv[6])
got = []
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="backend", protocol=mqtt.MQTTv311)
c.username_pw_set("backend", pw); c.tls_set(ca_certs=ca)
c.on_message = lambda cl, u, m: got.append(m.payload.decode())
c.connect("127.0.0.1", int(port)); c.loop_start()
c.subscribe("coil/szczecin/wol/pl-wol-relay/result", qos=1); time.sleep(0.5)
if payload:
    c.publish("coil/szczecin/wol/pl-wol-relay/cmd", payload, qos=1).wait_for_publish(5)
end = time.time() + timeout
while len(got) < count and time.time() < end:
    time.sleep(0.1)
c.loop_stop(); c.disconnect()
print("\n".join(got))
PY
}

pass=0; fail=0
ok() { echo "PASS  $1"; pass=$((pass + 1)); }
bad() { echo "FAIL  $1"; fail=$((fail + 1)); }

[[ "$(sub -t coil/szczecin/status/pl-wol-relay -C 1 -W 5 2>/dev/null)" == online ]] && ok "retained status online" || bad "retained status online"

now="$(date -u +%Y-%m-%dT%H:%M:%S.000Z)"; exp="$(date -u -d '+60 seconds' +%Y-%m-%dT%H:%M:%S.000Z)"
rid="$(python3 -c 'import uuid; print(uuid.uuid4())')"
cmd="{\"schema_version\":1,\"request_id\":\"$rid\",\"action\":\"wake\",\"target_id\":\"SZCZ-PC-01\",\"requested_by\":\"darek\",\"requested_at_utc\":\"$now\",\"expires_at_utc\":\"$exp\"}"
backend_cmd "$cmd" 3 30 > "$WORK/results1.txt"
states="$(python3 -c 'import json,sys; print(" ".join(json.loads(l)["state"] for l in open(sys.argv[1])))' "$WORK/results1.txt")"
[[ "$states" == "accepted packet_sent reachable" ]] && ok "test 1: accepted -> packet_sent -> reachable over the broker" || bad "test 1 states: $states"
grep -q "magic packet received" "$WORK/computer.log" && ok "fake computer received the magic packet" || bad "no magic packet"

backend_cmd "$cmd" 1 10 > "$WORK/results2.txt"
if cmp -s <(tail -n 1 "$WORK/results1.txt") "$WORK/results2.txt"; then ok "test 5: duplicate request_id re-publishes the identical final result"
else bad "test 5: duplicate result differs"; fi
packets="$(grep -c '"event": "result"' "$WORK/audit.jsonl" || true)"
[[ "$packets" == 4 ]] && ok "no second execution (4 result events in the audit log)" || bad "audit shows $packets result events"

backend_cmd "" 1 4 > "$WORK/results3.txt" & SUB=$!
sleep 0.8
set -a; source "$WORK/secrets.env"; set +a
pub -u SZCZ-TEMP-01 -P "$MQTT_SZCZ_TEMP_01_PASSWORD" -t coil/szczecin/wol/pl-wol-relay/cmd \
  -m "${cmd/$rid/$(python3 -c 'import uuid; print(uuid.uuid4())')}" 2>/dev/null || true
wait "$SUB" || true
[[ -z "$(tr -d '[:space:]' < "$WORK/results3.txt")" ]] && ok "test 8: sensor account cannot command the relay (ACL)" || bad "test 8: relay reacted to a sensor's command"

kill -9 "$RELAY_PID"; sleep 13
[[ "$(sub -t coil/szczecin/status/pl-wol-relay -C 1 -W 5 2>/dev/null)" == offline ]] && ok "test 9: Last Will sets status offline after kill -9" || bad "test 9: status not offline"

echo "Summary: $pass passed, $fail failed"
[[ "$fail" == 0 ]]
