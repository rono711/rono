# Shared ONOS controller and FRR DDS integration lab

This adds a standalone ONOS Classic service, with its own native GUI. It does not import or modify dashboard/policy_dashboard.py. The shared ONOS process can serve other project switches. Project-specific intent keys and a dedicated datapath isolate this first integration demonstration.

## Components

| Component | Name or endpoint | Responsibility |
|---|---|---|
| ONOS | rono-onos-shared; 127.0.0.1:8181 | Native GUI, REST API, intent compilation |
| OpenFlow | 127.0.0.1:6653 | ONOS control of the dedicated OVS bridge |
| OVS | br-rono-sdn; of:0000000000000175 | Controlled Ethernet transit, ports 1 and 2 |
| FRR A | rono-sdn-frr-a; AS65101 | 198.18.0.1/30; loopback 198.19.1.1/24 |
| FRR B | rono-sdn-frr-b; AS65102 | 198.18.0.2/30; loopback 198.19.2.1/24 |
| DDS adapter | rono-onos-dds; domain 175 | JSON connect/disconnect requests to scoped ONOS intents |
| Existing RONO | 127.0.0.1:8502 | Existing experiments; unchanged |

FRR A and B exchange BGP across ONOS-controlled L2 transit. ONOS programs OVS; it does not install FRR route maps in this demonstration. This is not the ONOS SDN-IP application or a native ONOS-to-FRR FPM route feed. The existing seven-router bgp7 topology remains a separate lab. Attaching its links to SDN transit requires a subsequent, project-specific topology change and new validation.

## Execute on EC2 as ubuntu

Run from the repository root:

```bash
python3 infrastructure/onos/shared_lab.py onos-up
python3 infrastructure/onos/shared_lab.py deps
python3 infrastructure/onos/shared_lab.py lab-up
python3 infrastructure/onos/shared_lab.py connect
python3 infrastructure/onos/shared_lab.py verify-connected
```

`deps` installs host Open vSwitch and enables its service. It attaches only dedicated veth interfaces; it never attaches the EC2 management NIC. `onos-up` pulls the released 2.7 image line and records the resolved image ID in its result; do not treat the floating tag as a cryptographic version lock. The container restarts unless stopped. Preserve it to retain its writable data; this setup does not promise state preservation if it is deleted/recreated, nor high availability.

In VS Code Remote SSH, open the Ports panel and forward remote **8181**. Keep it private. Open http://127.0.0.1:8181/onos/ui. The stock lab login is **onos / rocks**. ONOS is loopback-bound and should remain behind SSH. REST credentials can be overridden with ONOS_USER and ONOS_PASSWORD after changing them in ONOS itself; merely setting those environment variables does not change server credentials. Stop and recreate the DDS adapter after a credential change. No keys or custom passwords are written to this repository.

In ONOS, use the navigation menu to inspect **Topology**, **Devices**, **Flows**, and **Intents**. Device of:0000000000000175 should be available; the two rono-frr-demo keys should reach INSTALLED. FRR routers are IP peers behind the switch, not OpenFlow devices; automatic router-shaped topology icons are not promised. ONOS intent state alone is not proof of BGP or packet forwarding: the verification command also checks the FRR route and source-bound ping.

## DDS integration

After the direct REST test passes:

```bash
python3 infrastructure/onos/shared_lab.py dds-start
python3 infrastructure/onos/shared_lab.py dds-disconnect
python3 infrastructure/onos/shared_lab.py verify-disconnected
python3 infrastructure/onos/shared_lab.py dds-connect
python3 infrastructure/onos/shared_lab.py verify-connected
```

The DDS sender waits for matching discovery, publishes a unique request, and the host waits for the matching receiver log proving that ONOS applied/withdrew the two intents. It then requires a separate BGP/ping verification command. These are functional checks; no performance comparison is implied. DDS uses RELIABLE/VOLATILE/KEEP_LAST 20 on a separate topic and existing HelloWorld-generated string carrier. It does not reuse authorization-policy booleans and does not provide durable recovery or authenticated publishers. Only connect/disconnect for this fixed lab is accepted. Run one operation at a time. Do not independently edit the demo's intents during verification.

The adapter has no Docker socket and no FRR mutation privileges. It submits ONOS API requests only. The integration uses one owned OpenFlow switch, two project-prefixed intent keys, and two additional FRR containers. Future projects should allocate their own datapath IDs, keys, FRR names, ASNs, and DDS domains. ONOS itself is shared, so applications with global effects (for example reactive forwarding) require coordination. This demo refuses connect if reactive forwarding is active; it does not disable another project's app.

## Evidence and lifecycle

Each command writes results/20-onos-integration/<UTC timestamp>/result.json or error.json. Save these snapshots and image identities with the source before citing successful integration. Setup is locally syntax/mock tested; actual ONOS/OpenFlow/FRR/DDS runtime verification must occur on EC2.

```bash
python3 infrastructure/onos/shared_lab.py status
docker logs --tail 100 rono-onos-shared
docker logs --tail 100 rono-onos-dds
sudo ovs-vsctl show
```

For lab cleanup, while ONOS is running:

```bash
python3 infrastructure/onos/shared_lab.py lab-down
```

This withdraws only this demo's intents and removes its bridge/FRR/DDS containers. The shared ONOS service stays running. Do not delete or stop the shared controller when another project is using it. After a host restart, rerun lab-up to recreate container namespace links, then connect and verify-connected.

## Primary references

- https://wiki.onosproject.org/display/ONOS/Downloads
- https://hub.docker.com/r/onosproject/onos/tags/
- https://github.com/opennetworkinglab/onos/blob/master/Dockerfile
- https://github.com/opennetworkinglab/onos/blob/master/core/net/src/main/java/org/onosproject/net/intent/impl/compiler/PointToPointIntentCompiler.java
- https://docs.openvswitch.org/en/latest/faq/openflow/

The controller's live schema is available at http://127.0.0.1:8181/onos/v1/docs/. If the image API differs, use that schema and retain the failure evidence; do not bypass verification with manually inserted OVS forwarding flows.
