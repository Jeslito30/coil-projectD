#!/usr/bin/env bash
# Generate strong random passwords for every MQTT account and build Mosquitto's hashed password file.
#   config/passwd                  hashed file for the broker (git-ignored)
#   secrets/mqtt-passwords.env     plaintext passwords, mode 600 (git-ignored): hand them to the owners of each
#                                  simulator/service through a private channel; never post them in chat or Git.
# Usage: scripts/gen-passwd.sh [config-dir] [secrets-file] [--force]
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${1:-$ROOT/config}"
SECRETS="${2:-$ROOT/secrets/mqtt-passwords.env}"
FORCE="${3:-}"

# Accounts must match config/acl.
ACCOUNTS=(MATI-TEMP-01 MATI-HUM-01 MATI-AIR-01 SZCZ-TEMP-01 SZCZ-HUM-01 SZCZ-AIR-01 telegraf dashboard test-observer)

command -v mosquitto_passwd >/dev/null || { echo "mosquitto_passwd is required (package mosquitto / mosquitto-clients)." >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl is required." >&2; exit 1; }
if [[ -e "$SECRETS" && "$FORCE" != "--force" ]]; then
  echo "$SECRETS already exists. Re-run with --force to rotate ALL passwords (every client must be updated)." >&2
  exit 1
fi

umask 077
mkdir -p "$CONFIG_DIR" "$(dirname -- "$SECRETS")"
: > "$SECRETS"
rm -f "$CONFIG_DIR/passwd"

first=1
for account in "${ACCOUNTS[@]}"; do
  password="$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | cut -c1-32)"
  # Environment variable names: MQTT_<ACCOUNT>_PASSWORD, e.g. MQTT_MATI_TEMP_01_PASSWORD, MQTT_TELEGRAF_PASSWORD.
  # The observer uses the name expected by tests/security/mqtt_security_test.py.
  if [[ "$account" == "test-observer" ]]; then
    var="MQTT_OBSERVER_PASSWORD"
  else
    name="$(printf '%s' "$account" | tr 'a-z-' 'A-Z_')"
    var="MQTT_${name}_PASSWORD"
  fi
  printf '%s=%s\n' "$var" "$password" >> "$SECRETS"
  if [[ "$first" == 1 ]]; then
    mosquitto_passwd -b -c "$CONFIG_DIR/passwd" "$account" "$password"
    first=0
  else
    mosquitto_passwd -b "$CONFIG_DIR/passwd" "$account" "$password"
  fi
done

chmod 640 "$CONFIG_DIR/passwd"
chmod 600 "$SECRETS"
echo "Wrote ${#ACCOUNTS[@]} accounts."
echo "  broker password file: $CONFIG_DIR/passwd"
echo "  plaintext passwords:  $SECRETS   (keep private)"
