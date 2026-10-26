"""Local path validation, ONOS blocking, FRR recovery and restoration."""
import base64
import fcntl
import json
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
BASE = "http://127.0.0.1:8183/onos/v1"
DEV = "of:0000000000005501"
APP = "org.onosproject.cli"

lock = (ROOT / "projects/security/asap/live_lab/.live-test.lock").open("a")
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

out = ROOT / "results/22-security-asap" / datetime.now(
    timezone.utc
).strftime("%Y%m%dT%H%M%S%fZ-onos-frr-recovery")
out.mkdir(parents=True)
sequence = 0

def save(name, data):
    (out / name).write_text(json.dumps(data, indent=2))

def api(path, method="GET", data=None):
    headers = {
        "Accept": "application/json",
        "Authorization": "Basic " + base64.b64encode(b"onos:rocks").decode(),
    }
    raw = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        raw = json.dumps(data).encode()
    req = urllib.request.Request(
        BASE + path, headers=headers, data=raw, method=method
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        body = response.read()
        return json.loads(body) if body else {}

def command(args):
    global sequence
    sequence += 1
    r = subprocess.run(args, capture_output=True, text=True, timeout=20)
    save(f"command-{sequence:04d}.json", {
        "args": args, "returncode": r.returncode,
        "stdout": r.stdout, "stderr": r.stderr,
    })
    return r

def vty(node, *lines):
    args = ["docker", "exec", "clab-secasap-" + node, "vtysh"]
    for line in lines:
        args += ["-c", line]
    r = command(args)
    if r.returncode or "%" in r.stdout + r.stderr:
        raise RuntimeError(r.stdout + r.stderr)
    return r.stdout

def config(node, *lines):
    return vty(node, "configure terminal", *lines, "end")

def guard(enabled):
    line = "neighbor 10.222.4.1 route-map NO-LEAK out"
    config("leaker", "router bgp 65066",
           "address-family ipv4 unicast",
           line if enabled else "no " + line)
    vty("leaker", "clear bgp ipv4 unicast 10.222.4.1 soft out")

def paths(leaked):
    expected = (
        ["65066 65003 65169", "65002 65066 65003 65169"]
        if leaked else
        ["65001 65003 65169", "65002 65001 65003 65169"]
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        actual = []
        for node in ("validator", "observer"):
            data = json.loads(vty(
                node, "show bgp ipv4 unicast 192.0.2.0/24 json"
            ))
            best = [p for p in data.get("paths", [])
                    if p.get("bestpath", {}).get("overall")]
            actual.append(
                best[0]["aspath"]["string"] if len(best) == 1 else None
            )
        if actual == expected:
            return actual
        time.sleep(1)
    raise RuntimeError("Path mismatch: " + str(actual))

def ping(success):
    r = command([
        "docker", "exec", "clab-secasap-observer",
        "ping", "-I", "198.51.100.1",
        "-c", "3", "-W", "2", "192.0.2.1",
    ])
    count = 3 if success else 0
    if r.returncode != (0 if success else 1) or not re.search(
        rf"\b{count} (?:packets )?received\b", r.stdout
    ):
        raise RuntimeError("Unexpected ping result: " + r.stdout)

def bgp():
    data = json.loads(vty(
        "validator", "show bgp ipv4 unicast summary json"
    ))
    family = data.get("ipv4Unicast", data)
    if family.get("peers", {}).get("10.222.4.2", {}).get("state") != "Established":
        raise RuntimeError("Validator–leaker BGP session is not Established")

criteria = [
    {"type": "IN_PORT", "port": "1"},
    {"type": "ETH_TYPE", "ethType": "0x0800"},
    {"type": "IPV4_SRC", "ip": "198.51.100.1/32"},
    {"type": "IPV4_DST", "ip": "192.0.2.0/24"},
]

def canonical(items):
    result = {}
    for c in items:
        kind = c["type"]
        if kind in result:
            raise ValueError("Duplicate selector criterion")
        if kind == "IN_PORT":
            value = int(c["port"])
        elif kind == "ETH_TYPE":
            value = c["ethType"]
            value = int(value, 0) if isinstance(value, str) else int(value)
        elif kind in ("IPV4_SRC", "IPV4_DST"):
            value = c["ip"]
        else:
            value = json.dumps(c, sort_keys=True)
        result[kind] = value
    return result

def rules():
    return [
        f for f in api("/flows/" + DEV).get("flows", [])
        if f.get("appId") == APP
        and int(f.get("priority", -1)) == 50000
        and canonical(f.get("selector", {}).get("criteria", []))
            == canonical(criteria)
        and f.get("state") != "REMOVED"
    ]

def switch(name):
    r = command([
        "sudo", "ovs-ofctl", "-O", "OpenFlow13",
        "dump-flows", "br-rono-sec",
    ])
    if r.returncode:
        raise RuntimeError(r.stderr)
    (out / name).write_text(r.stdout)
    return r.stdout

def protected():
    found = rules()
    if len(found) != 1 or found[0].get("state") != "ADDED":
        raise RuntimeError("ONOS protection is not installed")
    text = switch("protected-flows.txt")
    drops = [
        line for line in text.splitlines()
        if "priority=50000" in line
        and "nw_src=198.51.100.1" in line
        and "nw_dst=192.0.2.0/24" in line
        and "actions=drop" in line
    ]
    if len(drops) != 1 or not re.search(r"n_packets=[1-9][0-9]*", drops[0]):
        raise RuntimeError("Switch drop-counter evidence missing")
    return found[0]["id"]

# Preflight performs no routing changes.
if rules() or "priority=50000" in switch("baseline-flows.txt"):
    raise RuntimeError("Existing priority-50000 rule; nothing changed")
if "neighbor 10.222.4.1 route-map NO-LEAK out" not in vty(
    "leaker", "show running-config"
):
    raise RuntimeError("Export guard absent")
running = vty("validator", "show running-config")
if "SEC-LEAK" in running or (
    "neighbor 10.222.4.2 route-map CUSTOMER-IN in" not in running
):
    raise RuntimeError("Validator is not in clean baseline state")
paths(False)
bgp()
ping(True)

save("manifest.json", {
    "scope": "Local validation and coordinated ONOS/FRR enforcement",
    "authorization_source": "Local lab relationships; not signed ASPA",
    "expected_stages": ["validation", "onos_block", "frr_recovery"],
    "timing": "Functional checks; polling is not convergence measurement",
})

error = None
cleanup_errors = []
flow_attempted = False
filter_attempted = False
stages = []

try:
    guard(False)
    observed = paths(True)
    ping(True)

    received = [int(a) for a in observed[0].split()]
    providers = {65169: [65003], 65003: [65001]}
    reverse = received[::-1]
    invalid = [
        [c, p] for c, p in zip(reverse, reverse[1:])
        if c in providers and p not in providers[c]
    ]
    if received != [65066, 65003, 65169] or not invalid:
        raise RuntimeError("Live authorization check failed")
    save("validation.json", {
        "decision": "INVALID", "received_path": received,
        "invalid_relationships": invalid, "providers": providers,
    })
    stages.append("validation")
    print("LIVE_LEAK_VALIDATION_PASS", flush=True)

    flow_attempted = True
    api("/flows/" + DEV + "?appId=" + APP, "POST", {
        "deviceId": DEV, "priority": 50000,
        "timeout": 0, "isPermanent": True,
        "selector": {"criteria": criteria},
        "treatment": {"instructions": []},
    })
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        found = rules()
        if len(found) == 1 and found[0].get("state") == "ADDED":
            break
        time.sleep(1)
    else:
        raise RuntimeError("Drop-rule installation timeout")

    ping(False)
    paths(True)
    bgp()
    drop_id = protected()
    stages.append("onos_block")
    print("ONOS_BLOCK_PASS", flush=True)

    pattern = "^" + "_".join(map(str, received)) + "$"
    filter_attempted = True
    config(
        "validator",
        "bgp as-path access-list SEC-LEAK permit " + pattern,
        "route-map SEC-LEAK-IN deny 5",
        "match ip address prefix-list VICTIM",
        "match as-path SEC-LEAK", "exit",
        "route-map SEC-LEAK-IN permit 10",
        "set local-preference 200", "exit",
        "router bgp 65002", "address-family ipv4 unicast",
        "neighbor 10.222.4.2 route-map SEC-LEAK-IN in",
    )
    vty("validator", "clear bgp ipv4 unicast 10.222.4.2 soft in")

    recovered = paths(False)
    ping(True)
    bgp()
    if protected() != drop_id:
        raise RuntimeError("Protection rule changed during recovery")
    if "neighbor 10.222.4.1 route-map NO-LEAK out" in vty(
        "leaker", "show running-config"
    ):
        raise RuntimeError("Leak was disabled before recovery verification")
    save("recovery.json", {
        "status": "PASS", "paths": recovered, "onos_drop_id": drop_id,
        "onos_protection_still_installed": True,
        "leak_export_still_enabled": True,
        "observer_ping": "3/3 received",
        "bgp_session": "Established",
    })
    stages.append("frr_recovery")
    print("FRR_RECOVERY_PASS; ONOS protection remains installed", flush=True)

except BaseException as exc:
    error = str(exc) or type(exc).__name__
finally:
    try:
        guard(True)
        paths(False)
    except Exception as exc:
        cleanup_errors.append("Export restoration: " + str(exc))

    if filter_attempted:
        try:
            config(
                "validator", "router bgp 65002",
                "address-family ipv4 unicast",
                "neighbor 10.222.4.2 route-map CUSTOMER-IN in",
            )
            running = vty("validator", "show running-config")
            if "route-map SEC-LEAK-IN " in running:
                config("validator", "no route-map SEC-LEAK-IN")
            running = vty("validator", "show running-config")
            if "bgp as-path access-list SEC-LEAK " in running:
                config("validator", "no bgp as-path access-list SEC-LEAK")
            vty("validator", "clear bgp ipv4 unicast 10.222.4.2 soft in")
        except Exception as exc:
            cleanup_errors.append("FRR cleanup: " + str(exc))

    if flow_attempted:
        try:
            for rule in rules():
                api("/flows/" + DEV + "/" + str(rule["id"]), "DELETE")
            deadline = time.monotonic() + 30
            while rules() and time.monotonic() < deadline:
                time.sleep(1)
            if rules():
                raise RuntimeError("ONOS rule remains")
        except Exception as exc:
            cleanup_errors.append("ONOS cleanup: " + str(exc))

    try:
        if "priority=50000" in switch("restored-flows.txt"):
            raise RuntimeError("OVS drop rule remains")
        running = vty("validator", "show running-config")
        if "SEC-LEAK" in running or (
            "neighbor 10.222.4.2 route-map CUSTOMER-IN in" not in running
        ):
            raise RuntimeError("Validator configuration not restored")
        if "neighbor 10.222.4.1 route-map NO-LEAK out" not in vty(
            "leaker", "show running-config"
        ):
            raise RuntimeError("Export guard not restored")
        paths(False)
        bgp()
        ping(True)
    except Exception as exc:
        cleanup_errors.append("Final verification: " + str(exc))

passed = (
    error is None and not cleanup_errors
    and stages == ["validation", "onos_block", "frr_recovery"]
)
result = {
    "status": "PASS" if passed else "FAIL",
    "stages_passed": stages,
    "error": error,
    "restoration": "PASS" if not cleanup_errors else "FAIL",
    "cleanup_errors": cleanup_errors,
    "scope": "Local validation → ONOS block → FRR recovery → restoration",
}
save("summary.json", result)
print(json.dumps(result, indent=2))
print("Evidence:", out.resolve())
if not passed:
    raise SystemExit(1)
