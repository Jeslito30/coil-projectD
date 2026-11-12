#!/usr/bin/env bash
# Create the project CA and the Mosquitto server certificate.
# The certificate is valid for every name a client uses to reach the broker:
#   10.66.0.1 (VPN address, simulators/members), mosquitto (Docker service name, Telegraf), localhost/127.0.0.1.
# Usage: scripts/gen-certs.sh [output-dir] [--force]      (default output-dir: ./certs)
set -Eeuo pipefail

OUT="${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/certs}"
FORCE="${2:-}"
SAN="${MQTT_CERT_SAN:-IP:10.66.0.1,IP:127.0.0.1,DNS:mosquitto,DNS:localhost}"

command -v openssl >/dev/null || { echo "openssl is required." >&2; exit 1; }
mkdir -p "$OUT"
if [[ -e "$OUT/ca.key" || -e "$OUT/server.key" ]] && [[ "$FORCE" != "--force" ]]; then
  echo "Certificates already exist in $OUT. Re-run with --force to replace them." >&2
  echo "Replacing the CA means every client must receive the new ca.crt." >&2
  exit 1
fi

umask 077
cd "$OUT"
openssl genrsa -out ca.key 4096 2>/dev/null
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 -subj "/CN=Project D MQTT CA" -out ca.crt
openssl genrsa -out server.key 2048 2>/dev/null
openssl req -new -key server.key -subj "/CN=10.66.0.1" -out server.csr
printf 'subjectAltName=%s\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n' "$SAN" > server.ext
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt \
  -days 825 -sha256 -extfile server.ext 2>/dev/null
rm -f server.csr server.ext ca.srl

chmod 644 ca.crt server.crt   # public parts
chmod 640 server.key          # private key: owner + group only
chmod 600 ca.key              # CA private key: keep it OFF the server (see README)

echo "Created in $OUT:"
ls -1 "$OUT"
echo
echo "Server certificate names:"
openssl x509 -in server.crt -noout -ext subjectAltName | tail -n +2
echo
echo "Distribute ONLY ca.crt to clients (simulators, Telegraf, members). Never share ca.key or server.key."
