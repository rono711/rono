#!/usr/bin/env python3
"""RONO networking module: discovery-only four-switch diamond.

Run: sudo python3 rono_lb_topology.py
Keep CLI open while inspecting ONOS. Type exit to stop these nodes.
No forwarding baseline or trained DRL policy is installed by this script.
"""
import fcntl
import json
import os
import socket
import subprocess
import sys

SWITCHES = ["rls1", "rls2", "rls3", "rls4"]
HOSTS = ["rlh1", "rlh2", "rlh3", "rlh4"]
DPIDS = [f"{0x5400 + n:016x}" for n in range(1, 5)]


def precheck():
    if os.geteuid() != 0:
        raise RuntimeError("Run with sudo python3.")
    bridges = subprocess.check_output(["ovs-vsctl", "list-br"], text=True).split()
    for bridge in bridges:
        dpid = subprocess.check_output(
            ["ovs-vsctl", "get", "bridge", bridge, "datapath_id"], text=True
        ).strip().strip('"')
        if bridge in SWITCHES or dpid in DPIDS:
            raise RuntimeError(f"Existing lab name/datapath collision: {bridge}. Nothing removed.")
    interfaces = json.loads(subprocess.check_output(["ip", "-j", "link", "show"], text=True))
    names = SWITCHES + HOSTS
    if any(i["ifname"].split("-eth")[0] in names for i in interfaces):
        raise RuntimeError("Existing lab interface found. Nothing removed; report this output.")
    with socket.create_connection(("127.0.0.1", 6654), timeout=5):
        pass


def main():
    if os.geteuid() != 0:
        raise RuntimeError("Run with sudo python3.")
    with open("/run/lock/rono-cn-lb.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        precheck()
        from mininet.cli import CLI
        from mininet.link import TCLink
        from mininet.log import setLogLevel
        from mininet.net import Mininet
        from mininet.node import OVSSwitch, RemoteController
        setLogLevel("info")
        net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                      build=False, cleanup=False, autoStaticArp=True)
        try:
            net.addController("rlc0", controller=RemoteController, ip="127.0.0.1", port=6654)
            sw = [net.addSwitch(name, dpid=dpid, protocols="OpenFlow13", failMode="secure")
                  for name, dpid in zip(SWITCHES, DPIDS)]
            hosts = [net.addHost(name, ip=f"10.54.0.{n}/24", mac=f"02:54:00:00:00:{n:02x}")
                     for n, name in enumerate(HOSTS, 1)]
            # Explicit port numbers are the contract for subsequent ONOS path installation.
            net.addLink(hosts[0], sw[0], port2=1, bw=1000)
            net.addLink(hosts[1], sw[0], port2=2, bw=1000)
            net.addLink(sw[0], sw[1], port1=3, port2=1, bw=20, delay="2ms", max_queue_size=100)
            net.addLink(sw[1], sw[3], port1=2, port2=1, bw=20, delay="2ms", max_queue_size=100)
            net.addLink(sw[0], sw[2], port1=4, port2=1, bw=20, delay="2ms", max_queue_size=100)
            net.addLink(sw[2], sw[3], port1=2, port2=2, bw=20, delay="2ms", max_queue_size=100)
            net.addLink(hosts[2], sw[3], port2=3, bw=1000)
            net.addLink(hosts[3], sw[3], port2=4, bw=1000)
            net.build()
            net.start()
            if not net.waitConnected(timeout=30):
                raise RuntimeError("Not all four switches connected to ONOS within 30 seconds.")
            print(json.dumps({
                "phase": "topology_discovery_only",
                "openflow_connected": True,
                "devices": ["of:" + d for d in DPIDS],
                "hosts": {name: f"10.54.0.{n}" for n, name in enumerate(HOSTS, 1)},
                "paths": ["rls1-rls2-rls4", "rls1-rls3-rls4"],
                "transit_link_mbps": 20,
                "note": "No forwarding policy installed; ping failure is expected at this stage."
            }, indent=2))
            print("Keep this terminal open. In another Ubuntu terminal query ONOS devices/links.")
            print("Type exit when finished; this stops only this Mininet instance.")
            CLI(net)
        finally:
            net.stop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
