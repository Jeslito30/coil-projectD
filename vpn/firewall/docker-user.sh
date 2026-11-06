#!/usr/bin/env bash
# Idempotently restrict Project D Docker-published ports arriving through wg0.
#
# Persistence: run this script from vpn/firewall/project-d-docker-user.service
# (after docker.service), NOT with `netfilter-persistent save`. Saving the full
# ruleset would also store Docker's own NAT/filter chains, which Docker recreates
# itself; restoring them at boot can leave stale or duplicated Docker rules.
set -Eeuo pipefail

[[ "${EUID}" -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
command -v iptables >/dev/null || { echo "iptables is required." >&2; exit 1; }
iptables -L DOCKER-USER -n >/dev/null 2>&1 || {
  echo "DOCKER-USER does not exist. Start Docker first; no rules changed." >&2; exit 1;
}

ENABLE_PHASE7="${ENABLE_PHASE7:-0}"

# Insert ACCEPT rules at the top so an existing final wg0 DROP cannot shadow them.
ensure_accept() {
  if ! iptables -C DOCKER-USER "$@" -j ACCEPT 2>/dev/null; then
    iptables -I DOCKER-USER 1 "$@" -j ACCEPT
  fi
}

remove_accept() {
  while iptables -C DOCKER-USER "$@" -j ACCEPT 2>/dev/null; do
    iptables -D DOCKER-USER "$@" -j ACCEPT
  done
}

# MQTT over TLS: member laptops and simulator hosts.
for ip in 10.66.0.11 10.66.0.12 10.66.0.15 10.66.0.21 10.66.0.22 10.66.0.25; do
  ensure_accept -i wg0 -s "$ip" -p tcp --dport 8883
done

# Grafana and team dashboard: member laptops and faculty.
for ip in 10.66.0.11 10.66.0.12 10.66.0.21 10.66.0.22 10.66.0.31 10.66.0.32; do
  ensure_accept -i wg0 -s "$ip" -p tcp -m multiport --dports 3000,8080
done

if [[ "$ENABLE_PHASE7" == "1" ]]; then
  # Monitoring agents and the Szczecin WoL relay require MQTT only when enabled.
  for ip in 10.66.0.16 10.66.0.41 10.66.0.42; do
    ensure_accept -i wg0 -s "$ip" -p tcp --dport 8883
  done
  # Member laptops and four separately enrolled phones use the backend API.
  for ip in 10.66.0.11 10.66.0.12 10.66.0.13 10.66.0.14 10.66.0.21 10.66.0.22 10.66.0.23 10.66.0.24; do
    ensure_accept -i wg0 -s "$ip" -p tcp --dport 8000
  done
else
  # Re-running with Phase 7 disabled also retracts its previously installed rules.
  for ip in 10.66.0.16 10.66.0.41 10.66.0.42; do
    remove_accept -i wg0 -s "$ip" -p tcp --dport 8883
  done
  for ip in 10.66.0.11 10.66.0.12 10.66.0.13 10.66.0.14 10.66.0.21 10.66.0.22 10.66.0.23 10.66.0.24; do
    remove_accept -i wg0 -s "$ip" -p tcp --dport 8000
  done
fi

# Everything else from the VPN to containers is dropped.
if ! iptables -C DOCKER-USER -i wg0 -j DROP 2>/dev/null; then
  iptables -A DOCKER-USER -i wg0 -j DROP
fi
# Return traffic is checked last so it ends up at the top of the chain.
ensure_accept -m conntrack --ctstate ESTABLISHED,RELATED

echo "Project D DOCKER-USER rules applied. ENABLE_PHASE7=$ENABLE_PHASE7"
iptables -S DOCKER-USER
echo "Verify positive and negative access, then repeat after reboot and Docker upgrades."
