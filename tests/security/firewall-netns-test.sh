#!/usr/bin/env bash
# Offline test of vpn/firewall/docker-user.sh with real packets, without Docker or WireGuard.
#
# It builds three throw-away network namespaces that model the Contabo host:
#   cli  (VPN peers: 10.66.0.x)  --wg0-->  srv (10.66.0.1, DNAT like Docker)  --br0-->  ctr (172.30.0.2, services)
# srv gets a Docker-like FORWARD -> DOCKER-USER chain that ENDS WITH "-j RETURN" (older Docker versions),
# then the script under test is applied inside srv and TCP connections are attempted from several peer addresses.
#
# Usage: sudo tests/security/firewall-netns-test.sh [path/to/docker-user.sh]
# Needs: root, iproute2, iptables, socat, python3. Nothing outside the temporary namespaces is changed.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="${1:-$ROOT/vpn/firewall/docker-user.sh}"
[[ "${EUID}" -eq 0 ]] || { echo "Run as root (network namespaces)." >&2; exit 1; }
for tool in ip iptables socat python3; do
  command -v "$tool" >/dev/null || { echo "$tool is required." >&2; exit 1; }
done

SUFFIX="pd$$"
SRV="srv-$SUFFIX"; CLI="cli-$SUFFIX"; CTR="ctr-$SUFFIX"
PORTS=(8883 3000 8088 8100 9999)
cleanup() {
  for ns in "$SRV" "$CLI" "$CTR"; do ip netns pids "$ns" 2>/dev/null | xargs -r kill 2>/dev/null || true; done
  for ns in "$SRV" "$CLI" "$CTR"; do ip netns del "$ns" 2>/dev/null || true; done
}
trap cleanup EXIT

for ns in "$SRV" "$CLI" "$CTR"; do ip netns add "$ns"; ip -n "$ns" link set lo up; done
ip link add "w$SUFFIX" type veth peer name "c$SUFFIX"
ip link set "w$SUFFIX" netns "$SRV"; ip link set "c$SUFFIX" netns "$CLI"
ip -n "$SRV" link set "w$SUFFIX" name wg0
ip link add "b$SUFFIX" type veth peer name "k$SUFFIX"
ip link set "b$SUFFIX" netns "$SRV"; ip link set "k$SUFFIX" netns "$CTR"

ip -n "$SRV" addr add 10.66.0.1/24 dev wg0; ip -n "$SRV" link set wg0 up
ip -n "$SRV" addr add 172.30.0.1/24 dev "b$SUFFIX"; ip -n "$SRV" link set "b$SUFFIX" up
ip netns exec "$SRV" sysctl -qw net.ipv4.ip_forward=1
ip -n "$CLI" addr add 10.66.0.21/24 dev "c$SUFFIX"; ip -n "$CLI" link set "c$SUFFIX" up
for extra in 10.66.0.10 10.66.0.22 10.66.0.31 10.66.0.16 10.66.0.50; do
  ip -n "$CLI" addr add "$extra/32" dev "c$SUFFIX"
done
ip -n "$CTR" addr add 172.30.0.2/24 dev "k$SUFFIX"; ip -n "$CTR" link set "k$SUFFIX" up
ip -n "$CTR" route add default via 172.30.0.1

# Docker-like plumbing: DNAT published ports, FORWARD jumps to DOCKER-USER first, which ends with RETURN.
for port in "${PORTS[@]}"; do
  ip netns exec "$SRV" iptables -t nat -A PREROUTING -d 10.66.0.1 -p tcp --dport "$port" -j DNAT --to-destination "172.30.0.2:$port"
  ip netns exec "$CTR" socat "TCP-LISTEN:$port,fork,reuseaddr" EXEC:'echo ok' &
done
ip netns exec "$SRV" iptables -N DOCKER-USER
ip netns exec "$SRV" iptables -A DOCKER-USER -j RETURN
ip netns exec "$SRV" iptables -A FORWARD -j DOCKER-USER
ip netns exec "$SRV" iptables -A FORWARD -j ACCEPT
sleep 0.5

reach() {  # reach SOURCE PORT -> prints open/closed
  ip netns exec "$CLI" python3 - "$1" "$2" <<'PY'
import socket, sys
s = socket.socket(); s.settimeout(1.5)
try:
    s.bind((sys.argv[1], 0)); s.connect(("10.66.0.1", int(sys.argv[2])))
    print("open" if s.recv(16).startswith(b"ok") else "closed")
except OSError:
    print("closed")
PY
}

pass=0; fail=0
expect() {  # expect SOURCE PORT open|closed LABEL
  local got; got="$(reach "$1" "$2")"
  if [[ "$got" == "$3" ]]; then echo "PASS  $4 ($1 -> :$2 $got)"; pass=$((pass + 1))
  else echo "FAIL  $4 ($1 -> :$2 expected $3, got $got)"; fail=$((fail + 1)); fi
}

echo "== Baseline: no Project D rules (everything reachable) =="
expect 10.66.0.50 8883 open "unrestricted before the script"

echo; echo "== Rules left by the previous script version (ACCEPTs at the top, DROP after RETURN) =="
ip netns exec "$SRV" iptables -I DOCKER-USER 1 -i wg0 -s 10.66.0.21 -p tcp --dport 8883 -j ACCEPT
ip netns exec "$SRV" iptables -I DOCKER-USER 1 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
ip netns exec "$SRV" iptables -A DOCKER-USER -i wg0 -j DROP
expect 10.66.0.50 8883 open "legacy DROP after RETURN is never reached"

echo; echo "== Apply $SCRIPT twice (idempotence), ENABLE_PHASE7=0 =="
ip netns exec "$SRV" env ENABLE_PHASE7=0 bash "$SCRIPT" >/dev/null
legacy="$(ip netns exec "$SRV" iptables -S DOCKER-USER | grep '^-A' | grep -vc -e '-j PROJECTD-WG$' -e '^-A DOCKER-USER -j RETURN$' || true)"
[[ "$legacy" == 0 ]] && { echo "PASS  legacy Project D rules removed from DOCKER-USER"; pass=$((pass + 1)); } \
                     || { echo "FAIL  $legacy legacy rules remain in DOCKER-USER"; fail=$((fail + 1)); }
ip netns exec "$SRV" env ENABLE_PHASE7=0 bash "$SCRIPT" >/dev/null
jumps="$(ip netns exec "$SRV" iptables -S DOCKER-USER | grep -c -- '-j PROJECTD-WG$' || true)"
[[ "$jumps" == 1 ]] && { echo "PASS  exactly one jump after two runs"; pass=$((pass + 1)); } \
                    || { echo "FAIL  expected one jump, found $jumps"; fail=$((fail + 1)); }
first="$(ip netns exec "$SRV" iptables -S DOCKER-USER | grep '^-A' | head -n 1)"
[[ "$first" == "-A DOCKER-USER -i wg0 -j PROJECTD-WG" ]] && { echo "PASS  jump is evaluated before Docker's RETURN"; pass=$((pass + 1)); } \
  || { echo "FAIL  first DOCKER-USER rule is: $first"; fail=$((fail + 1)); }
if ip netns exec "$SRV" env ENABLE_PHASE7=0 bash "$SCRIPT" --check >/dev/null; then
  echo "PASS  --check reports the installed policy as current"; pass=$((pass + 1))
else echo "FAIL  --check reported a mismatch"; fail=$((fail + 1)); fi

expect 10.66.0.10 8883 open   "Jeslito MQTT"
expect 10.66.0.21 8883 open   "Mykyta MQTT"
expect 10.66.0.22 8883 open   "Darek MQTT"
expect 10.66.0.21 3000 open   "member Grafana"
expect 10.66.0.31 3000 open   "faculty Grafana"
expect 10.66.0.31 8088 open   "faculty team dashboard"
expect 10.66.0.31 8883 closed "faculty has no MQTT"
expect 10.66.0.50 8883 closed "unknown peer MQTT"
expect 10.66.0.50 3000 closed "unknown peer Grafana"
expect 10.66.0.21 8100 closed "Phase 7 API closed while disabled"
expect 10.66.0.16 8883 closed "Phase 7 monitor MQTT closed while disabled"
expect 10.66.0.21 9999 closed "unlisted container port"

echo; echo "== ENABLE_PHASE7=1 =="
ip netns exec "$SRV" env ENABLE_PHASE7=1 bash "$SCRIPT" >/dev/null
expect 10.66.0.16 8883 open   "Phase 7 monitor MQTT"
expect 10.66.0.21 8100 open   "Phase 7 member API"
expect 10.66.0.31 8100 closed "faculty has no Phase 7 API"

echo; echo "== Back to ENABLE_PHASE7=0 retracts Phase 7 =="
ip netns exec "$SRV" env ENABLE_PHASE7=0 bash "$SCRIPT" >/dev/null
expect 10.66.0.16 8883 closed "Phase 7 monitor MQTT retracted"
expect 10.66.0.21 8100 closed "Phase 7 member API retracted"
expect 10.66.0.21 8883 open   "member MQTT still open"

echo; echo "== Tampering is detected by --check =="
ip netns exec "$SRV" iptables -I PROJECTD-WG 1 -s 10.66.0.50/32 -j RETURN
if ip netns exec "$SRV" env ENABLE_PHASE7=0 bash "$SCRIPT" --check >/dev/null; then
  echo "FAIL  --check missed an extra rule"; fail=$((fail + 1))
else echo "PASS  --check detects an unreviewed rule"; pass=$((pass + 1)); fi
ip netns exec "$SRV" env ENABLE_PHASE7=0 bash "$SCRIPT" >/dev/null
expect 10.66.0.50 8883 closed "re-applying removes the unreviewed rule"

echo; echo "== --remove rolls back =="
ip netns exec "$SRV" bash "$SCRIPT" --remove >/dev/null
expect 10.66.0.50 8883 open "unrestricted after --remove"

echo; echo "Summary: $pass passed, $fail failed"
[[ "$fail" == 0 ]]
