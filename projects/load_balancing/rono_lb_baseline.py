#!/usr/bin/env python3
"""Install/verify a fixed, equal-hop shortest-path baseline on CN ONOS only.
Run from ~/research/rono without sudo. Does not implement ECMP or DRL.
Static ARP is provided by rono_lb_topology.py; only IPv4 is forwarded here.
"""
import base64
import json
import os
from pathlib import Path
import sys
import time
import urllib.request
from datetime import datetime, timezone

ROOT = "http://127.0.0.1:8182/onos/v1"
PRIORITY = 20000
APP = "org.onosproject.cli"


def api(path, body=None):
    token = base64.b64encode((os.getenv("ONOS_USER", "onos") + ":" +
                            os.getenv("ONOS_PASSWORD", "rocks")).encode()).decode()
    headers = {"Authorization": "Basic " + token, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(ROOT + path, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=10) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def planned_rules():
    # Destination host -> egress port. Equal-hop tie is deliberately fixed via rls2.
    ports = {1: [1, 2, 3, 3], 2: [1, 1, 2, 2], 4: [1, 1, 3, 4]}
    return [{"deviceId": f"of:{0x5400 + switch:016x}", "priority": PRIORITY,
             "tableId": 0, "timeout": 0, "isPermanent": True,
             "selector": {"criteria": [
                 {"type": "ETH_TYPE", "ethType": "0x0800"},
                 {"type": "IPV4_SRC", "ip": "10.54.0.0/24"},
                 {"type": "IPV4_DST", "ip": f"10.54.0.{host}/32"}]},
             "treatment": {"instructions": [{"type": "OUTPUT", "port": str(port)}]}}
            for switch, outputs in ports.items()
            for host, port in enumerate(outputs, 1)]


def criteria(rule):
    entries = rule.get("selector", {}).get("criteria", [])
    return {c["type"]: (int(c["ethType"], 16) if c["type"] == "ETH_TYPE"
                        else c.get("ip")) for c in entries}


def matches(actual, desired):
    return (actual.get("deviceId") == desired["deviceId"] and
            actual.get("priority") == PRIORITY and
            int(actual.get("tableId", 0)) == 0 and
            criteria(actual) == criteria(desired) and
            actual.get("treatment", {}).get("instructions") ==
            desired["treatment"]["instructions"])


def main():
    rules = planned_rules()
    folder = Path("results/21-cn-load-balancing") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-fixed")
    folder.mkdir(parents=True, exist_ok=False)
    report = {"method": "fixed_shortest_path_via_rls2", "controller": ROOT,
              "is_drl": False, "data_plane_test": "PENDING: run Mininet pingall",
              "status": "ERROR", "planned_rules": rules}
    try:
        devices = api("/devices").get("devices", [])
        available = {d["id"] for d in devices if d.get("available")}
        expected = {f"of:{0x5400 + n:016x}" for n in range(1, 5)}
        if not expected <= available:
            raise RuntimeError("All four CN switches must be available on port 8182.")
        applications = api("/applications").get("applications", [])
        if any(a.get("name") in ("org.onosproject.fwd", "org.onosproject.ifwd")
               and a.get("state") == "ACTIVE" for a in applications):
            raise RuntimeError("Reactive forwarding is active; stop and report it before baseline installation.")
        before = api("/flows").get("flows", [])
        (folder / "flows-before.json").write_text(json.dumps(before, indent=2))
        pending = [r for r in rules if not any(matches(f, r) and
                   f.get("state") == "ADDED" for f in before)]
        # Refuse to overwrite an existing same-selector policy with different treatment.
        for r in rules:
            if any(f.get("deviceId") == r["deviceId"] and
                   f.get("state") not in ("REMOVED", "PENDING_REMOVE") and
                   f.get("priority", 0) >= PRIORITY and criteria(f) == criteria(r)
                   and not matches(f, r) for f in before):
                raise RuntimeError("Conflicting policy found; no baseline rules submitted.")
        if pending:
            api("/flows?appId=" + APP, {"flows": pending})
        deadline = time.monotonic() + 30
        while True:
            observed = api("/flows").get("flows", [])
            installed = [r for r in rules if any(matches(f, r) and
                         f.get("state") == "ADDED" for f in observed)]
            if len(installed) == len(rules):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Only {len(installed)}/12 rules verified ADDED. Report evidence; do not benchmark.")
            time.sleep(1)
        (folder / "flows-after.json").write_text(json.dumps(observed, indent=2))
        report.update(status="FLOW_INSTALL_PASS", verified_rules=len(installed))
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        (folder / "summary.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "planned_rules"}, indent=2))
    print("Evidence:", folder.resolve())
    return 0 if report["status"] == "FLOW_INSTALL_PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
