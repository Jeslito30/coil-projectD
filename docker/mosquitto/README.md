# Project D - Mosquitto TLS / ACL configuration

Broker configuration for the MQTT part of Project D, implementing `documents/data-design.md` (sections 4 and 8)
and `documents/vpn-plan.md` (section 3). Intended location in the repo: `docker/mosquitto/`
(the root `.gitignore` already ignores `passwd`, keys and server certificates, and allows `ca.crt`).

## What is in here

| Path | Purpose |
| --- | --- |
| `config/mosquitto.conf` | One TLS listener on 8883, no plaintext listener, no anonymous access, persistence on |
| `config/acl` | Per-sensor write rules; `telegraf` and `test-observer` read readings; `dashboard` reads status |
| `scripts/gen-certs.sh` | Creates the CA and a server certificate valid for `10.66.0.1`, `mosquitto`, `localhost`, `127.0.0.1` |
| `scripts/gen-passwd.sh` | Random password for each of the 9 accounts: hashed `config/passwd` + private `secrets/mqtt-passwords.env` |
| `scripts/acl-check.sh` | 7 extra ACL checks with `mosquitto_pub/sub` |
| `scripts/local-test.sh` | Starts a throw-away broker and runs everything, including `tests/security/mqtt_security_test.py` |

Accounts: `MATI-TEMP-01`, `MATI-HUM-01`, `MATI-AIR-01`, `SZCZ-TEMP-01`, `SZCZ-HUM-01`, `SZCZ-AIR-01`,
`telegraf`, `dashboard`, `test-observer`. Password variables are named `MQTT_<ACCOUNT>_PASSWORD`
(`MQTT_MATI_TEMP_01_PASSWORD`, `MQTT_TELEGRAF_PASSWORD`, ...); the observer uses `MQTT_OBSERVER_PASSWORD`.

## Deploy on the server (PH)

1. Generate certificates and passwords **on the server (or a trusted machine)**:
   ```bash
   scripts/gen-certs.sh            # -> certs/ca.crt, server.crt, server.key (+ ca.key)
   scripts/gen-passwd.sh           # -> config/passwd, secrets/mqtt-passwords.env
   ```
2. Move `certs/ca.key` somewhere safe and **off the server**; the broker only needs `ca.crt`, `server.crt`, `server.key`.
3. The container user (uid 1883) must be able to read the key and the password file:
   `sudo chown root:1883 certs/server.key config/passwd && sudo chmod 640 certs/server.key config/passwd`.
4. Compose service (adapt paths/volumes to the existing `compose.yml`):
   ```yaml
   mosquitto:
     image: eclipse-mosquitto:2.0.18
     restart: unless-stopped
     ports:
       - "10.66.0.1:8883:8883"     # VPN address only; use 127.0.0.1:18883:8883 until WireGuard is up
     volumes:
       - ./docker/mosquitto/config:/mosquitto/config:ro
       - ./docker/mosquitto/certs:/mosquitto/certs:ro
       - mosquitto-data:/mosquitto/data
   ```
5. Telegraf connects to `ssl://mosquitto:8883` with `tls_ca` = `ca.crt`; this works because the certificate contains `DNS:mosquitto`.
   Never use `insecure_skip_verify`.
6. Give each simulator owner only their own password (private channel, never Git or chat) and the public `ca.crt`.

## Verify

Local, before deploying (needs `mosquitto`, `mosquitto-clients`, `openssl`, `python3` + `paho-mqtt`):

```bash
scripts/local-test.sh path/to/tests/security/mqtt_security_test.py
```

Expected: 7/7 from `acl-check.sh` and 7/7 from the security test.
Against the real server, run `tests/security/mqtt_security_test.py` from a VPN peer as described in `tests/README.md`
and keep the dated result as evidence.

## Notes and limits

- Client IDs are not enforced by Mosquitto: the rule "one client ID per sensor" in the design is a convention, not a broker check.
- `test-observer` is for tests only; remove it from `config/passwd` outside test windows if the team prefers.
- Rotating passwords (`--force`) or replacing the CA requires updating every client.
- Phase 7 identities (WoL relay, monitoring) are intentionally not in the ACL; they need their own reviewed entries.
- Tested locally with Mosquitto 2.0.18, not yet on the Contabo server.
