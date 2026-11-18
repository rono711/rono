#!/usr/bin/env bash
set -euo pipefail

ROUTER="clab-bgp7-r3"

CONFIG=$(docker exec "$ROUTER" vtysh -c "show running-config")

if ! grep -q 'neighbor 10.0.36.2 route-map RONO_R6_IN in' <<< "$CONFIG"; then
    echo "Expected benchmark policy is not attached; refusing automatic cleanup."
    exit 1
fi

if ! grep -q 'ip prefix-list RONO_R6_PREFERRED seq 10 permit 10.105.0.0/24' <<< "$CONFIG" ||
   ! grep -q 'set local-preference 250' <<< "$CONFIG"; then
    echo "Unexpected policy contents; manual inspection required."
    exit 1
fi

docker exec "$ROUTER" vtysh \
  -c "configure terminal" \
  -c "router bgp 65003" \
  -c "address-family ipv4 unicast" \
  -c "no neighbor 10.0.36.2 route-map RONO_R6_IN in" \
  -c "exit-address-family" \
  -c "exit" \
  -c "no route-map RONO_R6_IN" \
  -c "no ip prefix-list RONO_R6_PREFERRED" \
  -c "end"

docker exec "$ROUTER" vtysh \
  -c "clear bgp ipv4 unicast 10.0.36.2 soft in"

echo "R3 benchmark policy removal requested."
echo "Verify BGP paths before declaring restoration complete."
