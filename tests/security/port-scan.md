# Project D — external exposure and port-scan record

**Date / timezone:**  
**Operator (role TBD):**  
**Target public IP:**  
**Scan source network:**  
**Cloud firewall / security-group revision:**  
**Server firewall revision:**  

## Expected Project D exposure

| Service | Expected public state | Expected VPN state / sources |
| --- | --- | --- |
| WireGuard `51820/udp` | Available for authenticated WireGuard peers | Hub on `10.66.0.1` |
| SSH `22/tcp` | No Project D public administration path | Member laptops `.11`, `.12`, `.21`, `.22` only; no forced `sshd ListenAddress 10.66.0.1` |
| MQTT TLS `8883/tcp` | Closed / not published | Approved members, simulators and explicitly enabled Phase 7 agents |
| Grafana `3000/tcp` | Closed / not published | Members and faculty |
| Team dashboard `8080/tcp` | Closed / not published | Members and faculty |
| Backend `8000/tcp` — Phase 7 | Closed / not published; absent in core | Member laptops and phones `.13`, `.14`, `.23`, `.24` when extension enabled |
| InfluxDB `8086/tcp` | Closed / no host or VPN binding | Docker internal network only |
| MQTT plaintext `1883/tcp` | Closed / no listener | Not enabled internally either |

## Commands and raw evidence

Record exact commands and save redacted output separately. Example TCP scan from an authorised external host:

```bash
nmap -Pn -sT -sV -p 22,1883,3000,8000,8080,8086,8883 <cloud-vm-public-ip>
```

For UDP, use an authorised, scoped scan and corroborate it:

```bash
nmap -Pn -sU -p 51820 <cloud-vm-public-ip>
wg show  # on the server, after a real approved client handshake
```

UDP `open|filtered` is not proof that a service is usable or absent. Confirm the intended state with cloud rules, `ss -lntup`, Docker port mappings, UFW/`DOCKER-USER`, and a real WireGuard handshake. Do not scan third-party networks without permission.

| Port / protocol | External scanner result | Host/cloud verification | Expected? | Evidence file |
| --- | --- | --- | --- | --- |
| `51820/udp` | | | | |
| `22/tcp` | | | | |
| `1883/tcp` | | | | |
| `3000/tcp` | | | | |
| `8000/tcp` | | | | |
| `8080/tcp` | | | | |
| `8086/tcp` | | | | |
| `8883/tcp` | | | | |

## Decision and exceptions

- [ ] Only intended Project D exposure is present.
- [ ] InfluxDB has no host `ports:` mapping.
- [ ] Core deployment has no backend listener; Phase 7 is separately controlled.
- [ ] Allowed and denied VPN-source tests agree with the access matrix.
- [ ] Results repeated after reboot and Docker/firewall persistence checks.

**Exceptions / remediation / retest date:**
