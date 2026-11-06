# Project D — WireGuard connection guide

Project D services are private. A VPN connection does not replace Grafana, SSH, JWT or MQTT credentials. Every laptop, phone, simulator host, relay and monitored computer needs its **own profile and key pair**; do not copy a profile between devices.

## 1. Request enrolment

1. Check that your local network and other VPNs do not already use `10.66.0.0/24`.
2. Create a new empty WireGuard tunnel locally so the application generates a private/public key pair.
3. Send the administrator only your **public key**, device type, custodian and requested peer name from `peers.md`.
4. Receive the assigned `/32`, server public key and cloud endpoint. Never send your private key or an exported full profile.

Phone reservations: PH `.13`–`.14`, PL `.23`–`.24`. Phones are Phase 7 peers and receive only backend API access. Monitoring reservations are Mati `.16` and Szczecin `.42`.

## 2. Configure the client

Use `client/client.conf.template` and fill it locally:

- `Address`: your assigned address with `/32`;
- `PrivateKey`: generated on this device;
- `PublicKey` in `[Peer]`: the **server's** public key;
- `Endpoint`: `<cloud-vm-public-ip>:51820`;
- `AllowedIPs`: `10.66.0.0/24` only;
- `PersistentKeepalive = 25` unless the administrator gives a different tested value.

### Windows

1. Install the official WireGuard application.
2. Choose **Add Tunnel → Add empty tunnel**, generate keys and complete the local configuration.
3. Save and activate the tunnel. Keep the exported configuration private.

### macOS

1. Install WireGuard from the official App Store.
2. Add an empty tunnel, generate keys and complete the configuration.
3. Activate it and confirm that only `10.66.0.0/24` is routed through the tunnel.

### Android / iOS

1. Install the official WireGuard application.
2. Create a tunnel **from scratch** on the phone; do not import a teammate's QR code or file.
3. Add the assigned address, locally generated private key and server peer details.
4. Activate the tunnel before opening the mobile application.

## 3. Verify access

- The application shows a recent handshake and increasing received bytes after contacting a service.
- Test only services allowed for your peer in `documents/vpn-plan.md`.
- Grafana: `http://10.66.0.1:3000` for authorised laptop/faculty profiles.
- Team dashboard: `http://10.66.0.1:8080` for authorised laptop/faculty profiles.
- Phase 7 backend for member laptops and enrolled phones: `http://10.66.0.1:8000` (or the HTTPS endpoint if TLS is later configured by the backend deployment).
- MQTT is for approved simulator/member/Phase 7 agent profiles, not faculty or phones.
- Normal internet browsing should continue outside the tunnel (split tunnel).

Record the peer name, date, handshake, allowed result and at least one denied result in the connectivity evidence. UDP scanner output alone is not proof of a working VPN; use a real handshake.

## 4. Troubleshooting

| Symptom | What to check |
| --- | --- |
| No handshake | Endpoint, UDP 51820, server/peer public keys, local clock and campus/home firewall |
| Handshake but service fails | Correct VPN address/port, service status, firewall entitlement and application login |
| Only one network has problems | `10.66.0.0/24` overlap, another VPN route, UDP filtering or MTU |
| TLS fails | Project CA, certificate SAN and correct server name; never disable verification |
| Phone opens no API | Phase 7 is enabled, phone uses its own `.13`/`.14`/`.23`/`.24` profile, backend port 8000 is allowed and JWT login is valid |

For a lost phone or laptop, notify the administrator immediately so that specific peer can be revoked without changing anyone else's profile.
