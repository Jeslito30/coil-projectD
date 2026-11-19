#!/usr/bin/env bash
# Idempotently restrict Project D Docker-published ports arriving through wg0.
#
# Design: all Project D rules live in a dedicated chain, PROJECTD-WG. DOCKER-USER gets exactly one
# jump at position 1 for traffic arriving on wg0. Every run rebuilds PROJECTD-WG atomically from the
# allow lists below, so the result does not depend on what DOCKER-USER already contains.
#
# Why not append a DROP to DOCKER-USER: on many Docker versions DOCKER-USER ends with "-j RETURN".
# A rule appended after that RETURN is never evaluated, so an appended DROP silently allows
# everything. The jump at position 1 is evaluated before any RETURN.
#
# Persistence: run this script from vpn/firewall/project-d-docker-user.service
# (after docker.service), NOT with `netfilter-persistent save`. Saving the full
# ruleset would also store Docker's own NAT/filter chains, which Docker recreates
# itself; restoring them at boot can leave stale or duplicated Docker rules.
#
# Usage: docker-user.sh            apply rules
#        docker-user.sh --check    verify the installed rules match this script; change nothing
#        docker-user.sh --remove   remove the Project D jump and chain (rollback)
# Environment: ENABLE_PHASE7=1 adds the optional Phase 7 allow rules; WG_IFACE (default wg0).
set -Eeuo pipefail

MODE="apply"
case "${1:-}" in
  "") ;;
  --check) MODE="check" ;;
  --remove) MODE="remove" ;;
  -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
  *) echo "Usage: $0 [--check|--remove]" >&2; exit 2 ;;
esac

[[ "${EUID}" -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
command -v iptables >/dev/null || { echo "iptables is required." >&2; exit 1; }
iptables -L DOCKER-USER -n >/dev/null 2>&1 || {
  echo "DOCKER-USER does not exist. Start Docker first; no rules changed." >&2; exit 1;
}

CHAIN="PROJECTD-WG"
WG_IFACE="${WG_IFACE:-wg0}"
ENABLE_PHASE7="${ENABLE_PHASE7:-0}"

# Allow lists. Keep them identical to documents/vpn-plan.md section 3 and vpn/peers.md.
MQTT_PEERS=(10.66.0.10 10.66.0.21 10.66.0.22)                         # members / simulator hosts
DASHBOARD_PEERS=(10.66.0.10 10.66.0.21 10.66.0.22 10.66.0.31 10.66.0.32) # members + faculty viewers
PHASE7_MQTT_PEERS=(10.66.0.16 10.66.0.41 10.66.0.42)                  # monitoring agents, WoL relay
PHASE7_API_PEERS=(10.66.0.10 10.66.0.12 10.66.0.13 10.66.0.14 10.66.0.21 10.66.0.22 10.66.0.23 10.66.0.24)

# Ports are matched on the ORIGINAL destination port (the published host port) through conntrack.
# DOCKER-USER sees packets after Docker's DNAT, so a plain --dport would match the container port
# and silently follow any change to a "host:container" mapping in compose.yml.
port_rule() {  # port_rule SOURCE_IP HOST_PORT
  echo "-A $CHAIN -s $1/32 -p tcp -m conntrack --ctorigdstport $2 --ctdir ORIGINAL -j RETURN"
}

# Desired content of PROJECTD-WG, one rule per line, in evaluation order.
desired_rules() {
  echo "-A $CHAIN -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN"
  local ip port
  for ip in "${MQTT_PEERS[@]}"; do port_rule "$ip" 8883; done
  for ip in "${DASHBOARD_PEERS[@]}"; do
    for port in 3000 8088; do port_rule "$ip" "$port"; done
  done
  if [[ "$ENABLE_PHASE7" == "1" ]]; then
    for ip in "${PHASE7_MQTT_PEERS[@]}"; do port_rule "$ip" 8883; done
    for ip in "${PHASE7_API_PEERS[@]}"; do port_rule "$ip" 8100; done
  fi
  # Everything else from the VPN to containers is dropped.
  echo "-A $CHAIN -j DROP"
}
# Allowed traffic RETURNs to DOCKER-USER and then continues through Docker's own FORWARD rules,
# so Docker's isolation rules still apply. ACCEPT would skip them.

JUMP=(-i "$WG_IFACE" -j "$CHAIN")

remove_legacy_rules() {
  # Rules written directly into DOCKER-USER by the previous version of this script.
  local rule
  while IFS= read -r rule; do
    [[ -n "$rule" ]] || continue
    # shellcheck disable=SC2086  # rule text comes from iptables -S and must be word-split
    iptables -D DOCKER-USER ${rule#-A DOCKER-USER }
  done < <(iptables -S DOCKER-USER | grep -E "^-A DOCKER-USER .*-i $WG_IFACE( |$)" | grep -v -- "-j $CHAIN\$" || true)
  while iptables -C DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; do
    iptables -D DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
  done
}

case "$MODE" in
  remove)
    while iptables -C DOCKER-USER "${JUMP[@]}" 2>/dev/null; do iptables -D DOCKER-USER "${JUMP[@]}"; done
    if iptables -L "$CHAIN" -n >/dev/null 2>&1; then iptables -F "$CHAIN"; iptables -X "$CHAIN"; fi
    echo "Project D rules removed. Docker-published VPN ports are no longer source-restricted."
    exit 0 ;;
  check)
    status=0
    if ! diff <(desired_rules) <(iptables -S "$CHAIN" 2>/dev/null | grep '^-A' || true) >/dev/null; then
      echo "MISMATCH: $CHAIN differs from the reviewed allow list (or is missing)."; status=1
    fi
    first="$(iptables -S DOCKER-USER | grep '^-A' | head -n 1)"
    if [[ "$first" != "-A DOCKER-USER -i $WG_IFACE -j $CHAIN" ]]; then
      echo "MISMATCH: the first DOCKER-USER rule is not the jump to $CHAIN (found: ${first:-none})."; status=1
    fi
    [[ "$status" == 0 ]] && echo "OK: installed rules match the reviewed policy (ENABLE_PHASE7=$ENABLE_PHASE7)."
    exit "$status" ;;
esac

# Apply: build the new chain under a temporary name, then swap the jump, so wg0 traffic is never
# left without a filter while the rules are being rebuilt.
NEW="${CHAIN}-NEW"
if iptables -L "$NEW" -n >/dev/null 2>&1; then iptables -F "$NEW"; iptables -X "$NEW"; fi
iptables -N "$NEW"
while IFS= read -r rule; do
  # shellcheck disable=SC2086  # generated rule text must be word-split into arguments
  iptables ${rule/-A $CHAIN /-A $NEW }
done < <(desired_rules)

iptables -I DOCKER-USER 1 -i "$WG_IFACE" -j "$NEW"
while iptables -C DOCKER-USER "${JUMP[@]}" 2>/dev/null; do iptables -D DOCKER-USER "${JUMP[@]}"; done
if iptables -L "$CHAIN" -n >/dev/null 2>&1; then iptables -F "$CHAIN"; iptables -X "$CHAIN"; fi
iptables -E "$NEW" "$CHAIN"          # the jump follows the rename
remove_legacy_rules

echo "Project D firewall applied: DOCKER-USER -> $CHAIN for $WG_IFACE (ENABLE_PHASE7=$ENABLE_PHASE7)"
iptables -S DOCKER-USER
iptables -S "$CHAIN"
echo "Verify positive and negative access (tests/security/port-scan.md); repeat after reboot and Docker upgrades."
