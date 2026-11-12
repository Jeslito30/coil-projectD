#!/usr/bin/env bash
# Start a throw-away local Mosquitto with this configuration and run the checks against it.
# Needs: mosquitto, mosquitto_passwd, mosquitto_pub/sub, openssl, python3 with paho-mqtt.
# Usage: scripts/local-test.sh [path/to/tests/security/mqtt_security_test.py]
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SEC_TEST="${1:-$ROOT/../../tests/security/mqtt_security_test.py}"
WORK="$(mktemp -d)"
PORT="${MQTT_TEST_PORT:-18883}"
BROKER_PID=""
cleanup() { [[ -n "$BROKER_PID" ]] && kill "$BROKER_PID" 2>/dev/null || true; rm -rf "$WORK"; }
trap cleanup EXIT

mkdir -p "$WORK/config" "$WORK/certs" "$WORK/data"
"$ROOT/scripts/gen-certs.sh" "$WORK/certs" >/dev/null
"$ROOT/scripts/gen-passwd.sh" "$WORK/config" "$WORK/secrets.env" >/dev/null
cp "$ROOT/config/acl" "$WORK/config/acl"
# Fully readable copies are fine for a throw-away test on one machine.
chmod 644 "$WORK/certs/"* "$WORK/config/"*
[[ "$(id -u)" == 0 ]] && chown -R root:root "$WORK"

# Same config, container paths replaced by temp paths, and a test port.
sed -e "s#/mosquitto/config#$WORK/config#g" -e "s#/mosquitto/certs#$WORK/certs#g" \
    -e "s#/mosquitto/data#$WORK/data#g" -e "s#^listener 8883#listener $PORT 127.0.0.1#" \
    "$ROOT/config/mosquitto.conf" > "$WORK/mosquitto.conf"
[[ "$(id -u)" == 0 ]] && echo "user root" >> "$WORK/mosquitto.conf"

mosquitto -c "$WORK/mosquitto.conf" > "$WORK/broker.log" 2>&1 &
BROKER_PID=$!
for _ in $(seq 1 30); do (echo > "/dev/tcp/127.0.0.1/$PORT") 2>/dev/null && break; sleep 0.3; done
(echo > "/dev/tcp/127.0.0.1/$PORT") 2>/dev/null || { echo "Broker did not start:"; cat "$WORK/broker.log"; exit 1; }
echo "Broker started on 127.0.0.1:$PORT"

echo; echo "== Server certificate names =="
openssl x509 -in "$WORK/certs/server.crt" -noout -ext subjectAltName | tail -n +2

echo; echo "== ACL checks (mosquitto_pub/sub) =="
status=0
"$ROOT/scripts/acl-check.sh" 127.0.0.1 "$PORT" "$WORK/certs/ca.crt" "$WORK/secrets.env" || status=1

if [[ -f "$SEC_TEST" ]]; then
  echo; echo "== tests/security/mqtt_security_test.py (7 checks) =="
  set -a; source "$WORK/secrets.env"; set +a
  MQTT_CA="$WORK/certs/ca.crt" MQTT_TEST_USERNAME=SZCZ-TEMP-01 MQTT_TEST_PASSWORD="$MQTT_SZCZ_TEMP_01_PASSWORD" \
  MQTT_OBSERVER_USERNAME=test-observer MQTT_BROKER=127.0.0.1 \
  python3 "$SEC_TEST" --tls-port "$PORT" --plaintext-port 1 \
    --own-topic coil/szczecin/temperature/SZCZ-TEMP-01 --other-topic coil/szczecin/humidity/SZCZ-HUM-01 \
    --output "$WORK/security-result.json" || status=1
else
  echo; echo "Security test script not found at $SEC_TEST (pass its path as the first argument)."
fi

echo; echo "== Broker log: denied/refused events =="
grep -iE "denied|not authorised|bad user|unknown protocol|certificate" "$WORK/broker.log" | head -10 || true
exit "$status"
