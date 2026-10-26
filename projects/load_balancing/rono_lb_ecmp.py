#!/usr/bin/env python3
"""Preprovision deterministic per-flow ECMP via CN ONOS REST (not DRL).

Requires sibling rono_lb_baseline.py. Uses SHA256 modulo two on canonical
bidirectional IPv4/TCP five-tuples. Does not search ports for a desired split.
The existing fixed-path rules remain fallback for unmatched/control traffic.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
from rono_lb_baseline import api

PRIORITY = 21000
FLOWS = [("10.54.0.1", 41001, "10.54.0.3", 5301),
         ("10.54.0.2", 41002, "10.54.0.4", 5302)]


def path_index(src, sport, dst, dport):
    endpoints = sorted([(src, sport), (dst, dport)])
    canonical = json.dumps([6, endpoints], separators=(",", ":"))
    return int.from_bytes(hashlib.sha256(canonical.encode()).digest()[:8], "big") % 2


def rules_for(src, sport, dst, dport):
    path = path_index(src, sport, dst, dport)
    middle = 2 if path == 0 else 3
    plans = [(1, 3 + path, src, sport, dst, dport),
             (middle, 2, src, sport, dst, dport),
             (4, int(dst.split(".")[-1]), src, sport, dst, dport),
             (4, 1 + path, dst, dport, src, sport),
             (middle, 1, dst, dport, src, sport),
             (1, int(src.split(".")[-1]), dst, dport, src, sport)]
    return [{"deviceId": f"of:{0x5400 + switch:016x}", "priority": PRIORITY,
             "tableId": 0, "timeout": 0, "isPermanent": True,
             "selector": {"criteria": [
                 {"type": "ETH_TYPE", "ethType": "0x0800"},
                 {"type": "IPV4_SRC", "ip": s + "/32"},
                 {"type": "IPV4_DST", "ip": d + "/32"},
                 {"type": "IP_PROTO", "protocol": 6},
                 {"type": "TCP_SRC", "tcpPort": sp},
                 {"type": "TCP_DST", "tcpPort": dp}]},
             "treatment": {"instructions": [{"type": "OUTPUT", "port": str(port)}]}}
            for switch, port, s, sp, d, dp in plans]


def signature(rule):
    criteria = []
    for c in rule.get("selector", {}).get("criteria", []):
        c = dict(c)
        for k in ("ethType", "protocol", "tcpPort"):
            if k in c:
                c[k] = int(str(c[k]), 16) if k == "ethType" else int(c[k])
        criteria.append(json.dumps(c, sort_keys=True))
    instructions = rule.get("treatment", {}).get("instructions", [])
    return (rule.get("deviceId"), int(rule.get("priority", 0)),
            int(rule.get("tableId", 0)), tuple(sorted(criteria)),
            json.dumps(instructions, sort_keys=True))


def main():
    rules = [r for f in FLOWS for r in rules_for(*f)]
    folder = Path("results/21-cn-load-balancing") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-ecmp")
    folder.mkdir(parents=True, exist_ok=False)
    report = {"method": "deterministic_software_per_flow_ECMP", "is_drl": False,
              "controller": "127.0.0.1:8182", "priority": PRIORITY,
              "flows": [{"src": s, "sport": sp, "dst": d, "dport": dp,
                         "path": ["rls1", "rls2" if path_index(s,sp,d,dp)==0 else "rls3", "rls4"]}
                        for s,sp,d,dp in FLOWS],
              "status": "ERROR", "data_plane_test": "PENDING"}
    try:
        active = {d["id"] for d in api("/devices").get("devices", []) if d.get("available")}
        if not {f"of:{0x5400+n:016x}" for n in range(1,5)} <= active:
            raise RuntimeError("Four available CN switches required on port 8182.")
        before = api("/flows").get("flows", [])
        (folder/"flows-before.json").write_text(json.dumps(before, indent=2))
        # Only submit missing exact rules; never remove discovery/fixed rules.
        present = {signature(f) for f in before if f.get("state") == "ADDED"}
        pending = [r for r in rules if signature(r) not in present]
        if pending:
            api("/flows?appId=org.onosproject.cli", {"flows": pending})
        deadline = time.monotonic()+30
        while True:
            after = api("/flows").get("flows", [])
            installed = {signature(f) for f in after if f.get("state") == "ADDED"}
            count = sum(signature(r) in installed for r in rules)
            if count == len(rules):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Only {count}/12 exact rules ADDED; do not benchmark.")
            time.sleep(1)
        (folder/"flows-after.json").write_text(json.dumps(after, indent=2))
        report.update(status="FLOW_INSTALL_PASS", verified_rules=count)
    except Exception as exc:
        report["error"] = str(exc)
    (folder/"summary.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print("Evidence:", folder.resolve())
    return 0 if report["status"] == "FLOW_INSTALL_PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
