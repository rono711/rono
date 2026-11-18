#!/usr/bin/env bash
set -euo pipefail

ROUTER="clab-bgp7-r3"
PREFIX="10.105.0.0/24"

docker inspect "$ROUTER" >/dev/null

# Refuse to replace an unrelated inbound policy.
CONFIG=$(docker exec "$ROUTER" vtysh -c "show running-config")
BINDINGS=$(printf '%s\n' "$CONFIG" |
  grep -E '^[[:space:]]*neighbor 10\.0\.36\.2 route-map .* in$' || true)

if [ -n "$BINDINGS" ]; then
    while IFS= read -r binding; do
        if ! printf '%s\n' "$binding" |
             grep -Eq '^[[:space:]]*neighbor 10\.0\.36\.2 route-map RONO_R6_IN in$'; then
            echo "ERROR: Conflicting inbound route-map exists" >&2
            exit 1
        fi
    done <<< "$BINDINGS"
fi


docker exec "$ROUTER" vtysh \
  -c "configure terminal" \
  -c "ip prefix-list RONO_R6_PREFERRED seq 10 permit $PREFIX" \
  -c "route-map RONO_R6_IN permit 10" \
  -c "match ip address prefix-list RONO_R6_PREFERRED" \
  -c "set local-preference 250" \
  -c "exit" \
  -c "route-map RONO_R6_IN permit 20" \
  -c "exit" \
  -c "router bgp 65003" \
  -c "address-family ipv4 unicast" \
  -c "neighbor 10.0.36.2 route-map RONO_R6_IN in" \
  -c "end"

docker exec "$ROUTER" vtysh \
  -c "clear bgp ipv4 unicast 10.0.36.2 soft in"

# Verify R3 preference and R2 alternative availability.
python3 - <<'VERIFY'
import json
import subprocess

def bgp(router):
    output = subprocess.check_output([
        "docker", "exec", router, "vtysh",
        "-c", "show bgp ipv4 unicast 10.105.0.0/24 json"
    ], text=True)
    return json.loads(output).get("paths", [])

def matching(paths, peer, aspath):
    return [
        p for p in paths
        if p.get("peer", {}).get("peerId") == peer
        and p.get("aspath", {}).get("string") == aspath
        and p.get("valid") is True
        and any(
            n.get("ip") == peer and n.get("accessible") is True
            for n in p.get("nexthops", [])
        )
    ]

r3 = matching(bgp("clab-bgp7-r3"), "10.0.36.2", "65006 65005")
r2_paths = bgp("clab-bgp7-r2")
alternative = matching(r2_paths, "10.0.23.2", "65003 65006 65005")
direct = matching(r2_paths, "10.0.25.2", "65005")

assert any(p.get("bestpath", {}).get("overall") for p in r3), "R3 preference failed"
assert alternative, "R2 alternative route missing"
assert any(p.get("bestpath", {}).get("overall") for p in direct), "R2 direct path not best"

print("RONO R3 setup verification PASS")
VERIFY
