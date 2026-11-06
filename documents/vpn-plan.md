# WireGuard VPN Addressing and Access Plan — Project D

**Version:** 0.2 · **Status:** Proposal — awaiting review by both pairs · **Updated:** 2026-11-04  
**Owner:** Joint (PL draft) · **Role allocation:** Pending · **Input needed:** Cloud VM public IP, server OS user and cloud-firewall details

---

## 1. Topology and routing policy

The network is hub-and-spoke. Every peer connects to the central cloud server; Project D services run on that server, so peers do not need direct access to one another.

- VPN subnet: **`10.66.0.0/24`**.
- Server WireGuard interface: **`wg0`, `10.66.0.1/24`**.
- Server listen port: **`51820/udp`**, the only Project D port intentionally exposed to the internet.
- Split tunnel: clients route **only `10.66.0.0/24`** through WireGuard. Normal internet traffic does not pass through the server.
- Peer isolation: the server MUST NOT forward traffic from one VPN peer to another.
- Server anti-spoofing: each server `[Peer]` entry has exactly one assigned VPN `/32` in `AllowedIPs`.

`10.66.0.0/24` avoids common home/campus ranges such as `192.168.0.0/24` and `192.168.1.0/24`, and common Docker defaults. This is a design choice, not a guarantee: before assigning a profile, check that the peer's local network and other active VPNs do not overlap. An overlap can break routing even when WireGuard itself is configured correctly.

## 2. Address allocation

| VPN IP | Peer name | Person / device | Role |
| --- | --- | --- | --- |
| `10.66.0.1` | `server` | Cloud VM | Hub and all core services |
| `10.66.0.11` | `ph-member-1` | Filipino member 1 laptop | Member / Mati administration |
| `10.66.0.12` | `ph-member-2` | Filipino member 2 laptop | Member / Mati administration |
| `10.66.0.13` | `ph-phone-1` | Filipino member 1 phone | Mobile backend access, Phase 7 only |
| `10.66.0.14` | `ph-phone-2` | Filipino member 2 phone | Mobile backend access, Phase 7 only |
| `10.66.0.15` | `mati-sim` | Device running the Mati simulator | Sensor publisher |
| `10.66.0.16` | `mati-monitor` | Optional monitored computer in Mati | Computer monitoring, Phase 7 only |
| `10.66.0.21` | `pl-member-1` | Polish member 1 laptop | Member / Szczecin administration |
| `10.66.0.22` | `pl-member-2` | Polish member 2 laptop | Member / Szczecin administration |
| `10.66.0.23` | `pl-phone-1` | Polish member 1 phone | Mobile backend access, Phase 7 only |
| `10.66.0.24` | `pl-phone-2` | Polish member 2 phone | Mobile backend access, Phase 7 only |
| `10.66.0.25` | `szcz-sim` | Device running the Szczecin simulator | Sensor publisher |
| `10.66.0.31` | `faculty-dorsu` | DOrSU faculty member | Dashboard viewer |
| `10.66.0.32` | `faculty-pm` | Maritime University of Szczecin faculty member | Dashboard viewer |
| `10.66.0.41` | `pl-wol-relay` | Optional WoL relay in Szczecin | WoL demonstration relay, Phase 7 only |
| `10.66.0.42` | `szcz-monitor` | Optional monitored computer in Szczecin | Computer monitoring, Phase 7 only |

Allocation ranges: `.10–.19` Mati, `.20–.29` Szczecin, `.30–.39` faculty and `.40–.49` optional extensions. Do not assign `.0` or `.255`; reserve remaining addresses deliberately rather than reusing them informally.

Each person or device receives an individual profile and key pair. Profiles MUST NOT be shared, including on phones. The four phone addresses are reserved for separate mobile devices and do not inherit access from a laptop profile. If a simulator runs on a member's laptop, it uses that member's VPN profile; leave the separate `*-sim` address unused and record that decision in `vpn/peers.md`. MQTT credentials remain per sensor even when a VPN profile is shared by several local simulator processes on that same host.

## 3. Service access matrix

Core services listen on `10.66.0.1` or only on the internal Docker network. A VPN address alone does not grant access: both network rules and application authentication apply.

| Service | Port | Listens on | Allowed VPN sources |
| --- | --- | --- | --- |
| WireGuard | `51820/udp` | Public interface | Internet; peer authentication still required |
| SSH | `22/tcp` | Server SSH interface, restricted by firewall | `.11`, `.12`, `.21`, `.22` |
| MQTT over TLS | `8883/tcp` | `10.66.0.1` | `.11`, `.12`, `.15`, `.21`, `.22`, `.25`; add `.16`, `.41` / `.42` only when their Phase 7 roles require it |
| Grafana | `3000/tcp` | `10.66.0.1` | `.11`, `.12`, `.21`, `.22`, `.31`, `.32` |
| Team dashboard | `8080/tcp` | `10.66.0.1` | `.11`, `.12`, `.21`, `.22`, `.31`, `.32` |
| Backend API — **Phase 7 extension** | `8000/tcp` | `10.66.0.1` | `.11`, `.12`, `.13`, `.14`, `.21`, `.22`, `.23`, `.24`; JWT authentication still required |
| InfluxDB | `8086/tcp` | Docker internal network only | Telegraf and Grafana containers; no VPN or host binding |
| MQTT plaintext | `1883/tcp` | **Not enabled anywhere** | None |
| Restic backup | No listening port | Outbound from the server to the approved repository | Not a VPN client service |

Faculty peers can reach only the dashboards. Team phones can reach only the Phase 7 backend API; they receive no implicit SSH, MQTT, Grafana or team-dashboard access. Denied network paths remain denied even if someone obtains application credentials. Conversely, reaching an allowed port never replaces SSH, Grafana, JWT or MQTT authentication.

Telegraf connects to Mosquitto over TLS like every other MQTT client. No plaintext internal listener is configured, so the test “unencrypted MQTT connections are rejected” passes by design rather than by relying only on firewall rules.

## 4. Server implementation notes

### 4.1 Safe rollout prerequisites

1. Confirm the VM's public IP, cloud-provider firewall/security-group rules and an emergency console or other recovery path.
2. Configure and test `wg0` before restricting SSH to the VPN. If administration is performed over SSH, do not enable a policy that can remove the only working session without a verified alternative.
3. Use versioned, reviewable scripts in `vpn/firewall/` rather than manually accumulating rules. Scripts should be idempotent and should preserve unrelated existing rules.
4. Record the pre-change state and test both allowed and denied access after every change. A cloud firewall can expose a port even when host rules look correct, and vice versa.

Recommended UFW policy after a safe administration path is confirmed:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 51820/udp
```

Do not run `ufw enable` until the required return path for the current administration session has been verified.

`vpn/firewall/ufw-rules.sh` implements this section: it adds the allow rules before the default-deny policy, is a dry run unless `--apply` is given, and refuses to apply on a host where UFW is **already active** unless `--confirm-vpn-ssh` confirms that a second SSH session over `10.66.0.1` and the provider's emergency console both work. On an active UFW, `default deny incoming` blocks new public SSH logins immediately.

### 4.2 Docker publishing and UFW

Docker publishes ports through its own NAT/filter rules; a broad UFW deny does not reliably protect a mapping such as `"3000:3000"`. Bind every published Project D service explicitly to the VPN address and leave InfluxDB unpublished:

```yaml
services:
  grafana:
    ports:
      - "10.66.0.1:3000:3000"
  mosquitto:
    ports:
      - "10.66.0.1:8883:8883"
  team-dashboard:
    ports:
      - "10.66.0.1:8080:8080"
  # Phase 7 only:
  # backend:
  #   ports:
  #     - "10.66.0.1:8000:8000"
  influxdb:
    # No host "ports:" mapping.
    expose:
      - "8086"  # Documentation only; Docker networking already allows container access.
```

Binding to `10.66.0.1` requires `wg0` to exist before Docker starts. Add `/etc/systemd/system/docker.service.d/after-wireguard.conf`:

```ini
[Unit]
After=wg-quick@wg0.service
Wants=wg-quick@wg0.service
```

Then reload systemd and restart Docker through the normal administration procedure. Verify the dependency after a full reboot. Note that `Wants=` is not a hard failure dependency; health checks must still detect a missing VPN interface.

### 4.3 Host rules: SSH

The following examples implement the SSH rows of the matrix. Add them only after the safe-rollout checks above:

```bash
sudo ufw allow in on wg0 from 10.66.0.11 to 10.66.0.1 port 22 proto tcp
sudo ufw allow in on wg0 from 10.66.0.12 to 10.66.0.1 port 22 proto tcp
sudo ufw allow in on wg0 from 10.66.0.21 to 10.66.0.1 port 22 proto tcp
sudo ufw allow in on wg0 from 10.66.0.22 to 10.66.0.1 port 22 proto tcp
```

Do **not** restrict `sshd` with `ListenAddress 10.66.0.1` as part of this plan. If SSH starts before WireGuard, that address may not exist and remote recovery after reboot can fail. Enforce VPN-only Project D administration with firewall rules while retaining a tested console or other emergency recovery path.

### 4.4 Container rules: `DOCKER-USER`

For Docker-published ports (`8883`, `3000`, `8080`, and Phase 7 `8000`), enforce source restrictions in the `DOCKER-USER` chain. The following is a minimal policy fragment for a dedicated server; integrate it into the reviewed firewall script rather than repeatedly pasting it into a live terminal:

```bash
# Return traffic first. The production script must avoid duplicate entries.
sudo iptables -I DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# MQTT: members and simulator hosts.
for ip in 10.66.0.11 10.66.0.12 10.66.0.15 10.66.0.21 10.66.0.22 10.66.0.25; do
  sudo iptables -A DOCKER-USER -i wg0 -s "$ip" -p tcp --dport 8883 -j ACCEPT
done

# Grafana and team dashboard: members and faculty.
for ip in 10.66.0.11 10.66.0.12 10.66.0.21 10.66.0.22 10.66.0.31 10.66.0.32; do
  sudo iptables -A DOCKER-USER -i wg0 -s "$ip" -p tcp -m multiport --dports 3000,8080 -j ACCEPT
done

# Phase 7 only — backend: member laptops and individually enrolled phones.
# for ip in 10.66.0.11 10.66.0.12 10.66.0.13 10.66.0.14 10.66.0.21 10.66.0.22 10.66.0.23 10.66.0.24; do
#   sudo iptables -A DOCKER-USER -i wg0 -s "$ip" -p tcp --dport 8000 -j ACCEPT
# done

# Deny other VPN-originated traffic to forwarded container ports.
sudo iptables -A DOCKER-USER -i wg0 -j DROP
```

Host and container ports are identical in this plan. If Compose changes a container-side port, review the actual post-DNAT rule rather than assuming the host port still matches.

**Persistence:** apply the rules with `vpn/firewall/docker-user.sh` from the systemd unit `vpn/firewall/project-d-docker-user.service`, which runs after `docker.service` and again whenever Docker restarts. Do **not** use `netfilter-persistent save` for these rules: it would also save Docker's own NAT/filter chains, and restoring them at boot can leave stale or duplicated Docker rules. Re-test after reboot and Docker upgrades.

### 4.5 No forwarding between peers

Set this on the server even when split tunnelling is used; client `AllowedIPs` is not a server-side isolation control:

```ini
# /etc/wireguard/wg0.conf, [Interface]
PostUp   = iptables -A FORWARD -i wg0 -o wg0 -j DROP
PostDown = iptables -D FORWARD -i wg0 -o wg0 -j DROP
```

Make this idempotent in the production configuration and check interaction with any existing forwarding policy. The intended result is that a client can reach approved services on `10.66.0.1` but cannot use the server as a router to another peer.

## 5. Configuration templates

### 5.1 Server `/etc/wireguard/wg0.conf`

```ini
[Interface]
Address    = 10.66.0.1/24
ListenPort = 51820
PrivateKey = <actual server private key; stored only on the server>
PostUp     = iptables -A FORWARD -i wg0 -o wg0 -j DROP
PostDown   = iptables -D FORWARD -i wg0 -o wg0 -j DROP

# One [Peer] block per active row of the address table.
[Peer]
# pl-member-1
PublicKey  = <public key supplied by pl-member-1>
AllowedIPs = 10.66.0.21/32

[Peer]
# faculty-pm
PublicKey  = <public key supplied by faculty-pm>
AllowedIPs = 10.66.0.32/32
```

`PrivateKey` requires the key value, not a file path. The real file belongs in `/etc/wireguard/` with restrictive permissions and MUST NOT be committed. A Git template may contain placeholders only.

### 5.2 Client profile — example `pl-member-1`

```ini
[Interface]
Address    = 10.66.0.21/32
PrivateKey = <generated locally; never transmitted or committed>
MTU        = 1420

[Peer]
PublicKey           = <server public key>
Endpoint            = <cloud-vm-public-ip>:51820
AllowedIPs          = 10.66.0.0/24
PersistentKeepalive = 25
```

`PersistentKeepalive = 25` helps maintain NAT state for peers behind home or campus routers. `MTU = 1420` is a starting value, not a universal optimum; if connections handshake but large TLS packets stall, test path MTU and lower it only with documented evidence.

## 6. Key and peer lifecycle

### 6.1 Generate and enrol

1. On Linux, each user or device generates its own key pair locally:
   ```bash
   umask 077
   wg genkey | tee wg.key | wg pubkey > wg.pub
   ```
   On Windows, macOS, Android and iOS, create a new empty tunnel in the WireGuard application and let it generate the key pair locally.
2. Send only the **public** key, requested peer name and device owner to the administrator through the agreed channel.
3. The administrator checks that the address and public key are unique, adds one `[Peer]` block and records the entry in `vpn/peers.md`.
4. The administrator returns only the server public key, endpoint and assigned VPN address. The client adds these to its local profile.
5. Apply the server change without disrupting unrelated peers, then complete the connectivity checklist.

### 6.2 Revoke, replace and rotate

- For a lost device, departed member or suspected compromise, remove or disable that peer immediately, verify it can no longer connect and mark the registry entry revoked with the date.
- Issue a new key pair and profile for a replacement device; do not reuse an old private key.
- Rotate the server key only through a planned maintenance window because every client must receive the new server public key. Rotate individual peer keys without changing other peers.
- Never place private keys, exported profiles or password-equivalent configuration in chat, email, Notion, screenshots, tickets or Git. Prefer local profile creation over QR/file transfer; any justified transfer mechanism requires a separate secure procedure and deletion of temporary exports.

## 7. Time synchronisation for delay measurements

| Machine | Tool | Minimum check | Target absolute offset |
| --- | --- | --- | --- |
| Cloud VM | chrony | `chronyc tracking` and `chronyc sources -v` | < 5 ms |
| Linux simulator host / Raspberry Pi | chrony | Same checks, recorded per host | < 10 ms |
| VirtualBox guest | chrony plus awareness of host/guest drift | Measure immediately before and after the test | < 10 ms and stable during the run |
| Windows simulator host | Approved, actively monitored time-sync method | Record measured offset and stability | Accept only if the measurement requirement is demonstrated; do not assume default sync is sufficient |

The delay-test operator, once roles are assigned, records offsets for the server and every simulator host at the start of each delay test and, for long or virtualised runs, at the end. The report must include actual values, synchronisation source and test timestamps.

Clock error directly affects `delay_ms`. Estimate and report total uncertainty conservatively, for example as the sum of the absolute server and simulator offset bounds plus observed drift. Negative computed delays are possible when clocks disagree; preserve them as diagnostic evidence rather than silently clamping them to zero.

## 8. Connection and security test checklist

Complete this for every peer and link the dated evidence:

- [ ] Registry entry contains owner, device, peer name, `/32`, public key and enrolment date; no private key is present.
- [ ] `wg show` or the application displays a recent handshake (normally < 2 minutes while active).
- [ ] `ping 10.66.0.1` succeeds if ICMP is allowed by the host policy; otherwise use an approved TCP service check and record the policy.
- [ ] Each allowed service in section 3 is reachable with valid application credentials.
- [ ] Representative denied services fail. Faculty must not reach SSH or MQTT; simulator-only peers must not reach dashboards unless separately authorised.
- [ ] Another peer's VPN address is unreachable through the server.
- [ ] Normal internet browsing still works and its public egress does not change unexpectedly (split tunnel).
- [ ] A route-overlap check was performed on the client.
- [ ] Results are stored in `evidences/vpn-connectivity.md` or the corresponding dated test artifact.

After deployment, perform an external scan and an on-host review (`ss`, Docker port mappings, UFW, `DOCKER-USER` and cloud firewall). UDP scan results can be ambiguous (`open|filtered`), so corroborate them with configuration and a real WireGuard handshake rather than treating scanner output alone as proof.

## 9. Troubleshooting guide

| Symptom | Likely checks |
| --- | --- |
| No handshake | Endpoint and UDP 51820 reachability, cloud/host firewall, correct server and peer public keys, local clock, WireGuard service/application state |
| Handshake but no service | Server `wg0` address, client route, service binding to `10.66.0.1`, UFW / `DOCKER-USER`, application credentials |
| Works at one site but not another | Local subnet overlap, other VPN routes, campus NAT/firewall, MTU and path-specific UDP filtering |
| Handshake works but TLS stalls | Path MTU, certificate SAN/CA validation, MQTT client TLS logs |
| Peer can reach another peer | Missing or ineffective `FORWARD -i wg0 -o wg0` drop; inspect active rules after reboot |
| Delay is negative or unstable | Clock offsets/drift, queued messages after reconnect, processing load; do not label the result pure network latency |
