# Project D — WireGuard peer registry

**Public information only.** Never paste private keys, exported profiles, passwords or QR codes into this file. “Owner” identifies the device custodian, not an implementation role. Role allocation: see the task assignment agreed on 9 Nov 2026 (PH deploys on the server, PL prepares peer profiles).

Reserved addresses are not active peers. Add a public key and enrolment date only after the device generates its own key pair and enrolment is approved.

## Server

| Item | Value |
| --- | --- |
| Interface | `wg0`, `10.66.0.1/24`, UDP `51820` |
| Server public key | `DQUHsmYKM1uRkt6emmTbVfrx5uSAH5BISvLAuLkUlmA=` (posted by Jeslito, 13 Nov 2026) |
| Endpoint | Sent privately by the server admin; not stored in this public repository |

## Active peers

The mappings below match the server enrolment and `wg show` output reviewed on 14 Nov 2026. Public keys are identifiers, not secrets.

| Peer | Device / custodian | VPN IP | Public key | Added (UTC) | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `ph-member-1` | PH laptop — Jeslito | `10.66.0.10` | `nHfsvPbYsyS4jWI7NTCkH7v8rfXWPXQ/rlz0oUsUnkM=` | 2026-11-14 | Active | Core member access; key and handshake confirmed |
| `pl-member-2` | PL laptop — Mykyta | `10.66.0.21` | `ywsxkPjLONDKGGnZFMnnrrd0lnw6sDJPGcwqFcwFmBE=` | 2026-11-13 | Active | Core member access; key and handshake confirmed |
| `pl-member-1` | PL computer — Darek | `10.66.0.22` | `XCUOQAGSdXSNAean5wy9psXdg1Vb4kl4bZo/+UbI8WE=` | 2026-11-14 | Active | Also runs the Szczecin simulator |

## Reserved addresses

| Peer | Device / custodian | VPN IP | Status | Notes |
| --- | --- | --- | --- | --- |
| `ph-member-2` | PH laptop — Lea | `10.66.0.12` | On hold | Laptop damaged; generate a new key pair on the replacement device |
| `ph-phone-1` | PH phone 1 — custodian TBD | `10.66.0.13` | Phase 7 reserved | Backend only; separate profile |
| `ph-phone-2` | PH phone 2 — custodian TBD | `10.66.0.14` | Phase 7 reserved | Backend only; separate profile |
| `mati-sim` | Mati simulator host | `10.66.0.15` | Reserved | Leave unused if simulator uses a member laptop |
| `mati-monitor` | Mati monitored computer | `10.66.0.16` | Phase 7 reserved | Monitoring agent |
| `pl-phone-1` | PL phone 1 — custodian TBD | `10.66.0.23` | Phase 7 reserved | Backend only; separate profile |
| `pl-phone-2` | PL phone 2 — custodian TBD | `10.66.0.24` | Phase 7 reserved | Backend only; separate profile |
| `szcz-sim` | Szczecin simulator host | `10.66.0.25` | Not used | Simulator runs on `pl-member-1` (`10.66.0.22`) |
| `faculty-dorsu` | DOrSU faculty device | `10.66.0.31` | Reserved | Dashboard viewer |
| `faculty-pm` | PM Szczecin faculty device | `10.66.0.32` | Reserved | Dashboard viewer |
| `pl-wol-relay` | Szczecin demonstration relay | `10.66.0.41` | Phase 7 reserved | A Mati deployment needs a separate relay/allocation |
| `szcz-monitor` | Szczecin monitored computer | `10.66.0.42` | Phase 7 reserved | Monitoring agent |

`10.66.0.11` is intentionally unassigned: Jeslito's enrolled peer is `10.66.0.10`. Do not reuse active addresses without first revoking the old peer.

## Enrolment record requirements

For each activation, record the public key, date and custodian. To revoke access, remove the server `[Peer]` block, apply the change with `wg syncconf`, verify that the device no longer handshakes, then change the status to **Revoked** and add the revocation date in Notes. Never reuse a revoked private key on a replacement device.

## Status values

`Reserved` → `Key requested` → `Key received — awaiting server enrolment` → `Active` (after `vpn/add-peer.sh --apply` on the server and a successful handshake) → `Revoked`.
