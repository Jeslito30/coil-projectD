# Wake-on-LAN Relay — MQTT Contract (Phase 7, optional)

**Owner:** Poland pair (relay side) · **Consumer:** Philippines pair (backend and Flutter app)
**Status:** Draft v0.1 — awaiting review by both pairs
**Scope:** Messages between the FastAPI backend and the Wake-on-LAN relay in the Szczecin LAN. The REST API between the Flutter app and the backend is defined by the PH pair; section 9 only lists what it needs from this contract.

Phase 7 starts only after all required Project D deliverables work (Implementation Plan, priority 6).

---

## 1. Why a relay

A Wake-on-LAN magic packet is a Layer 2 broadcast. It does not cross routers or the WireGuard tunnel. A small always-on device inside the target LAN (the relay) receives the request over MQTT and sends the magic packet locally. Sending a packet is not proof that the computer started: the relay reports success only when the target actually answers.

## 2. Identities and network

| Identity | MQTT username / client ID | VPN address | Role |
| --- | --- | --- | --- |
| WoL relay (Szczecin) | `pl-wol-relay` | `10.66.0.41` | Receives commands, sends magic packets, reports results |
| Backend | `backend` | runs on the server (Docker network) | Publishes commands, reads results |

The relay connects to `10.66.0.1:8883` with TLS and the project CA, exactly like a simulator. Its MQTT port access is enabled by `ENABLE_PHASE7=1` in `vpn/firewall/docker-user.sh`. Passwords are generated with the other MQTT accounts and stored outside Git.

## 3. Topics

```text
coil/szczecin/wol/pl-wol-relay/cmd       backend  -> relay   commands
coil/szczecin/wol/pl-wol-relay/result    relay    -> backend progress and final result
coil/szczecin/status/pl-wol-relay        relay    -> all     retained online/offline (Last Will)
```

| Topic | QoS | Retain |
| --- | --- | --- |
| `.../cmd` | 1 | **false** — a retained command would be executed again after every relay restart |
| `.../result` | 1 | false |
| `coil/szczecin/status/pl-wol-relay` | 1 | true |

## 4. ACL entries (to add to `docker/mosquitto/config/acl` when Phase 7 starts)

```text
# Phase 7 - Wake-on-LAN relay (Szczecin)
user pl-wol-relay
topic read coil/szczecin/wol/pl-wol-relay/cmd
topic write coil/szczecin/wol/pl-wol-relay/result
topic write coil/szczecin/status/pl-wol-relay

# Phase 7 - backend
user backend
topic write coil/szczecin/wol/pl-wol-relay/cmd
topic read coil/szczecin/wol/pl-wol-relay/result
topic read coil/+/status/+
```

No other identity may write commands. The relay cannot read readings or write anywhere else.

## 5. Command message (`.../cmd`)

```json
{"schema_version":1,"request_id":"6f1c2a4e-8d3b-4f7a-9c61-2b5e0d9a7f13","action":"wake","target_id":"SZCZ-PC-01","requested_by":"darek","requested_at_utc":"2026-11-25T08:30:00.123Z","expires_at_utc":"2026-11-25T08:31:00.123Z"}
```

| Field | Type | Rule |
| --- | --- | --- |
| `schema_version` | integer | Exactly `1` |
| `request_id` | string | UUID v4 created by the backend; unique per request |
| `action` | string | Exactly `wake`. No shutdown or restart actions in v1 |
| `target_id` | string | Logical name from the relay's local allowlist, e.g. `SZCZ-PC-01`. **No MAC address in the message** |
| `requested_by` | string | Backend user name, for the audit log. No e-mail addresses or other personal data |
| `requested_at_utc` | string | ISO 8601 UTC, three fractional digits, `Z` |
| `expires_at_utc` | string | `requested_at_utc` + 60 s. The relay rejects expired commands |

Why: `expires_at_utc` stops a command queued during an outage from waking a computer hours later. The allowlist means a compromised backend can still only wake computers the relay owner approved.

## 6. Result messages (`.../result`)

The relay publishes progress messages and exactly **one final** message per `request_id`.

```json
{"schema_version":1,"request_id":"6f1c2a4e-8d3b-4f7a-9c61-2b5e0d9a7f13","relay_id":"pl-wol-relay","target_id":"SZCZ-PC-01","state":"reachable","final":true,"reason":null,"packet_sent_at_utc":"2026-11-25T08:30:00.410Z","reachable_at_utc":"2026-11-25T08:30:41.902Z","wake_seconds":41.5,"check":"icmp","timestamp_utc":"2026-11-25T08:30:41.905Z"}
```

| `state` | `final` | Meaning |
| --- | --- | --- |
| `accepted` | false | Command is valid; relay is about to send the packet |
| `packet_sent` | false | Magic packet sent 3 times, 1 s apart |
| `reachable` | true | Target answered the reachability check — **the only success state** |
| `unreachable_timeout` | true | Packet sent, but no answer within 180 s |
| `already_on` | true | Target already answered before the packet was sent; nothing was sent |
| `rejected` | true | Command not executed; see `reason` |

| `reason` (only with `rejected`) | Meaning |
| --- | --- |
| `unknown_target` | `target_id` is not in the allowlist |
| `expired` | Received after `expires_at_utc` |
| `duplicate` | `request_id` already processed (QoS 1 redelivery); the relay re-publishes the earlier final result instead |
| `rate_limited` | Same target woken less than 60 s ago |
| `malformed` | Invalid JSON, fields or types |
| `unsupported_action` | `action` other than `wake` |

| Field | Type | Rule |
| --- | --- | --- |
| `schema_version` | integer | `1` |
| `request_id` | string | From the command; for `malformed` commands without a readable ID: `null` |
| `relay_id` | string | `pl-wol-relay` |
| `target_id` | string or null | From the command |
| `state` | string | From the table above |
| `final` | boolean | `true` only for the final state |
| `reason` | string or null | Only for `rejected` |
| `packet_sent_at_utc` | string or null | Time of the first packet |
| `reachable_at_utc` | string or null | Time of the first successful check |
| `wake_seconds` | number or null | `reachable_at_utc` − `packet_sent_at_utc`, one decimal |
| `check` | string | Reachability method, e.g. `icmp` or `tcp:3389` |
| `timestamp_utc` | string | Time this result message was created |

## 7. Relay behaviour

1. Subscribe to `.../cmd` with QoS 1; publish retained `online` on connect, Last Will `offline`.
2. Validate the command (section 5). Reject in this order: `malformed`, `unsupported_action`, `expired`, `duplicate`, `unknown_target`, `rate_limited`.
3. Publish `accepted`. If the target already answers the check, publish `already_on` and stop.
4. Send the magic packet (6 × `FF` + 16 × target MAC) as UDP broadcast to `255.255.255.255:9`, 3 times, 1 s apart. Publish `packet_sent`.
5. Check reachability every 5 s for up to 180 s. Publish `reachable` or `unreachable_timeout`.
6. Keep processed `request_id`s for 24 h (duplicate detection) and the last wake time per target (rate limit).
7. Process one command per target at a time; a second command for a busy target is `rate_limited`.
8. Log every command and result locally with timestamps (audit log for evidence).

The allowlist lives only on the relay, outside Git (`target_id` → MAC address, reachability check, owner who gave permission).

## 8. Backend behaviour (PH side)

- Create `request_id` and `expires_at_utc`; publish the command once with QoS 1.
- Treat the request as `relay_offline` if the relay status is `offline` before sending, or if no `accepted` arrives within 10 s.
- Show progress states; treat only `reachable` as success. Display `packet_sent` as "packet sent, waiting for the computer", never as "computer on".
- Store every request and final result in the audit log (who, when, target, result).

## 9. What the REST API needs from this contract

| REST side (PH defines) | Maps to |
| --- | --- |
| Start a wake request for a `target_id` | Command message, returns `request_id` |
| Read request status | Latest `state` / `final` for that `request_id` |
| List allowed targets | Static list agreed with the relay owner (names only, no MAC addresses) |
| Relay availability | Retained `coil/szczecin/status/pl-wol-relay` |

## 10. Known limitations

- The target must use a wired Ethernet connection; WoL over Wi-Fi is generally not supported.
- BIOS/UEFI must allow WoL; ErP / deep-sleep settings usually have to be disabled.
- After a full power loss many computers do not wake by WoL until they have been powered on once. For the "power returns, the computer must start" case in Mati, the BIOS option "Restore on AC power loss = Power On" is the appropriate setting; WoL covers computers that were shut down normally.
- The relay itself, the router and the switch must be powered and running.
- The target needs a fixed IP address (DHCP reservation) for the reachability check.

## 11. Acceptance tests

| # | Test | Expected |
| --- | --- | --- |
| 1 | Valid command, target off | `accepted` → `packet_sent` → `reachable`; `wake_seconds` recorded |
| 2 | Valid command, target already on | `accepted` → `already_on`; no packet sent |
| 3 | Unknown `target_id` | `rejected` / `unknown_target` |
| 4 | Command older than `expires_at_utc` | `rejected` / `expired` |
| 5 | Same `request_id` sent twice | Second one: earlier final result re-published, no second packet |
| 6 | Two commands for the same target within 60 s | Second: `rejected` / `rate_limited` |
| 7 | Target unplugged from power | `packet_sent` → `unreachable_timeout` after 180 s |
| 8 | `SZCZ-TEMP-01` tries to publish to `.../cmd` | Denied by the broker ACL |
| 9 | Relay process killed | Retained status becomes `offline` (Last Will) |

Record screenshots or logs of each test in `evidence/` with the date.
