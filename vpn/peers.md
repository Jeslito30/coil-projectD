# Project D — WireGuard peer registry

**Public information only.** Never paste private keys, exported profiles, passwords or QR codes into this file. “Owner” identifies the device custodian, not an implementation role. Role allocation remains pending.

Reserved addresses are not active peers. Add a public key and enrolment date only after the device generates its own key pair and enrolment is approved.

| Peer | Device / custodian | VPN IP | Public key | Added (UTC) | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `ph-member-1` | PH laptop 1 — custodian TBD | `10.66.0.11` | — | — | Reserved | Core member access |
| `ph-member-2` | PH laptop 2 — custodian TBD | `10.66.0.12` | — | — | Reserved | Core member access |
| `ph-phone-1` | PH phone 1 — custodian TBD | `10.66.0.13` | — | — | Phase 7 reserved | Backend only; separate profile |
| `ph-phone-2` | PH phone 2 — custodian TBD | `10.66.0.14` | — | — | Phase 7 reserved | Backend only; separate profile |
| `mati-sim` | Mati simulator host | `10.66.0.15` | — | — | Reserved | Leave unused if simulator uses a member laptop |
| `mati-monitor` | Mati monitored computer | `10.66.0.16` | — | — | Phase 7 reserved | Monitoring agent |
| `pl-member-1` | PL laptop 1 — custodian TBD | `10.66.0.21` | — | — | Reserved | Core member access |
| `pl-member-2` | PL laptop 2 — custodian TBD | `10.66.0.22` | — | — | Reserved | Core member access |
| `pl-phone-1` | PL phone 1 — custodian TBD | `10.66.0.23` | — | — | Phase 7 reserved | Backend only; separate profile |
| `pl-phone-2` | PL phone 2 — custodian TBD | `10.66.0.24` | — | — | Phase 7 reserved | Backend only; separate profile |
| `szcz-sim` | Szczecin simulator host | `10.66.0.25` | — | — | Reserved | Leave unused if simulator uses a member laptop |
| `faculty-dorsu` | DOrSU faculty device | `10.66.0.31` | — | — | Reserved | Dashboard viewer |
| `faculty-pm` | PM Szczecin faculty device | `10.66.0.32` | — | — | Reserved | Dashboard viewer |
| `pl-wol-relay` | Szczecin demonstration relay | `10.66.0.41` | — | — | Phase 7 reserved | A Mati deployment needs a separate relay/allocation |
| `szcz-monitor` | Szczecin monitored computer | `10.66.0.42` | — | — | Phase 7 reserved | Monitoring agent |

## Enrolment record requirements

For each activation, record the public key, date and custodian. To revoke access, remove the server `[Peer]` block, apply the change with `wg syncconf`, verify that the device no longer handshakes, then change the status to **Revoked** and add the revocation date in Notes. Never reuse a revoked private key on a replacement device.
