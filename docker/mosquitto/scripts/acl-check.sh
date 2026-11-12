#!/usr/bin/env bash
# Extra ACL checks with mosquitto_pub / mosquitto_sub (complements tests/security/mqtt_security_test.py).
# Usage: scripts/acl-check.sh <host> <port> <ca.crt> <secrets.env>
set -uo pipefail
HOST="$1"; PORT="$2"; CA="$3"; SECRETS="$4"
set -a; source "$SECRETS"; set +a

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0
ok()  { echo "PASS  $1"; pass=$((pass+1)); }
bad() { echo "FAIL  $1"; fail=$((fail+1)); }

mq_pub() { mosquitto_pub -h "$HOST" -p "$PORT" --cafile "$CA" -u "$1" -P "$2" -t "$3" -m "$4" -q 1 ${5:-} >/dev/null 2>&1; }
mq_sub() { mosquitto_sub -h "$HOST" -p "$PORT" --cafile "$CA" -u "$1" -P "$2" -t "$3" -v -q 1 -C 1 -W 4 > "$4" 2>/dev/null & }

TEMP_MATI="coil/mati/temperature/MATI-TEMP-01"
reading='{"schema_version":1,"sensor_id":"MATI-TEMP-01","city":"Mati","sensor_type":"temperature","timestamp_utc":"2026-11-10T08:30:00.123Z","seq":0,"value":28.5,"unit":"C","simulated":true}'

# 1. A sensor can publish its retained status and the dashboard account can read it.
mq_pub SZCZ-TEMP-01 "$MQTT_SZCZ_TEMP_01_PASSWORD" coil/szczecin/status/SZCZ-TEMP-01 online -r
mq_sub dashboard "$MQTT_DASHBOARD_PASSWORD" 'coil/+/status/+' "$TMP/1"; wait
grep -q "online" "$TMP/1" && ok "dashboard reads retained status written by a sensor" || bad "dashboard reads retained status"

# 2. The dashboard account must not read sensor readings.
mq_sub dashboard "$MQTT_DASHBOARD_PASSWORD" 'coil/+/temperature/+' "$TMP/2"; sleep 1
mq_pub MATI-TEMP-01 "$MQTT_MATI_TEMP_01_PASSWORD" "$TEMP_MATI" "$reading"; wait
[[ ! -s "$TMP/2" ]] && ok "dashboard cannot read reading topics" || bad "dashboard received a reading"

# 3. Telegraf can read readings.
mq_sub telegraf "$MQTT_TELEGRAF_PASSWORD" 'coil/+/temperature/+' "$TMP/3"; sleep 1
mq_pub MATI-TEMP-01 "$MQTT_MATI_TEMP_01_PASSWORD" "$TEMP_MATI" "$reading"; wait
grep -q "MATI-TEMP-01" "$TMP/3" && ok "telegraf reads reading topics" || bad "telegraf could not read readings"

# 4. Telegraf must not write readings.
mq_sub test-observer "$MQTT_OBSERVER_PASSWORD" "$TEMP_MATI" "$TMP/4"; sleep 1
mq_pub telegraf "$MQTT_TELEGRAF_PASSWORD" "$TEMP_MATI" "$reading"; wait
[[ ! -s "$TMP/4" ]] && ok "telegraf cannot publish readings" || bad "telegraf published a reading"

# 5. A sensor cannot read (not even its own topic).
mq_sub MATI-TEMP-01 "$MQTT_MATI_TEMP_01_PASSWORD" "$TEMP_MATI" "$TMP/5"; sleep 1
mq_pub MATI-TEMP-01 "$MQTT_MATI_TEMP_01_PASSWORD" "$TEMP_MATI" "$reading"; wait
[[ ! -s "$TMP/5" ]] && ok "sensor has no read access" || bad "sensor could read a topic"

# 6. A Szczecin sensor cannot write a Mati status topic.
mq_sub dashboard "$MQTT_DASHBOARD_PASSWORD" 'coil/mati/status/MATI-HUM-01' "$TMP/6"; sleep 1
mq_pub SZCZ-TEMP-01 "$MQTT_SZCZ_TEMP_01_PASSWORD" coil/mati/status/MATI-HUM-01 offline; wait
[[ ! -s "$TMP/6" ]] && ok "cross-city status write is denied" || bad "cross-city status write was delivered"

# 7. Anonymous connections are refused.
mosquitto_pub -h "$HOST" -p "$PORT" --cafile "$CA" -t "$TEMP_MATI" -m x -q 1 >/dev/null 2>&1 \
  && bad "anonymous publish accepted" || ok "anonymous connection refused"

echo "Summary: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
