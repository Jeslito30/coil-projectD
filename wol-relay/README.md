# Wake-on-LAN relay — Phase 7 (optional)

`wol_relay.py` implements the relay side of [documents/wol-contract.md](../documents/wol-contract.md) v0.1. It is **optional Phase 7 work**: deploy it only after the mandatory Project D baseline and the restore test have passed. The backend that sends commands (PH side) and the Flutter app do not exist yet, so the relay is implemented and tested but not in use.

## What it does

| Contract rule | Implementation |
| --- | --- |
| Identity, topics, retain (2, 3) | Client ID/username `pl-wol-relay`; subscribes to `.../cmd` with QoS 1; retained `online`, Last Will `offline`; retained commands are ignored and logged |
| Validation order (7.2) | `malformed` → `unsupported_action` → `expired` → `unknown_target` → `rate_limited`; `request_id` must be a UUID v4; `expires_at_utc` must be after `requested_at_utc` and at most 60 s later; `requested_by` must not be an e-mail address |
| Allowlist (5, 7) | `target_id` → MAC/IP/check only in the local `config.yaml`; results never contain a MAC address |
| Wake (7.3–7.5) | Checks reachability first (`already_on`), sends the packet 3 times 1 s apart, polls every 5 s for up to 180 s; only `reachable` is success |
| Exactly one final result (6, 7.2, 7.6) | SQLite state: a duplicate `request_id` re-publishes the stored final result without executing again, also after a restart; results kept 24 h |
| Rate limit (7.6, 7.7) | Last wake per target is persisted; a second wake within 60 s or while the target is busy is `rate_limited` |
| Crash safety | A request left unfinished by a crash is finalised as `rejected`/`expired` on start, never re-executed |
| Audit log (7.8) | `wol-relay-audit.jsonl`: every command, result and connection event with a UTC timestamp |

## Setup on the relay device (Szczecin, VPN `10.66.0.41`)

The relay must sit in the same LAN as the target computers (wired Ethernet target, WoL enabled in BIOS/UEFI, fixed IP).

```bash
cd wol-relay
python3 -m venv .venv && . .venv/bin/activate
pip install -r ../simulators/requirements.txt          # paho-mqtt, PyYAML
cp config.example.yaml config.yaml && $EDITOR config.yaml
python3 wol_relay.py --config config.yaml --send-test-packet SZCZ-PC-01   # LAN-only check, no MQTT
MQTT_PL_WOL_RELAY_PASSWORD=... python3 wol_relay.py --config config.yaml
```

Server side, before the relay can connect:

1. Add the ACL entries from contract section 4 to `docker/mosquitto/config/acl` and create the `pl-wol-relay` and `backend` accounts with `mosquitto_passwd` (outside Git).
2. Enrol the relay peer `10.66.0.41` and apply the firewall with `ENABLE_PHASE7=1`.

## Tests

```bash
python3 -m unittest discover -s wol-relay/tests -v      # 17 offline tests: contract tests 1-7, validation, persistence
sudo wol-relay/tests/broker-test.sh                     # real Mosquitto with TLS + Phase 7 ACL: tests 1, 5, 8, 9
```

`broker-test.sh` uses a fake computer that only opens its check port after receiving a correct magic packet, so `reachable` depends on the packet. Last result (18 Nov 2026, Mosquitto 2.0.18): 7/7.

Not yet tested: a real computer waking from S5 over a real LAN, and the backend side of the contract. Record those runs with dates in `evidence/` before reporting Phase 7 as working.
