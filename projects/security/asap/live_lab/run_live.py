"""One isolated FRR leak/filter/restoration cycle; not native ASPA."""
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import json
import subprocess
import time

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "results/22-security-asap" / datetime.now(
    timezone.utc
).strftime("%Y%m%dT%H%M%S%fZ-live-automated")
OUT.mkdir(parents=True)

lock = (ROOT / "projects/security/asap/live_lab/.live-test.lock").open("a")
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
counter = 0

def command(node, args):
    global counter
    counter += 1
    cmd = ["docker", "exec", "clab-secasap-" + node] + args
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=30
    )
    record = {
        "command": cmd, "returncode": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
    }
    (OUT / f"command-{counter:04d}.json").write_text(
        json.dumps(record, indent=2)
    )
    text = result.stdout + result.stderr
    if result.returncode:
        raise RuntimeError(f"{node}: command failed: {text}")
    return result.stdout

def vty(node, *lines):
    args = ["vtysh"]
    for line in lines:
        args.extend(["-c", line])
    output = command(node, args)
    if any(s in output for s in (
        "% Unknown", "% Ambiguous", "% Can't",
        "processing failure", "datastore locked",
    )):
        raise RuntimeError(f"{node}: FRR command error: {output}")
    return output

def configure(node, *lines):
    return vty(node, "configure terminal", *lines, "end")

def restore():
    # Guard the leak before removing the validator's temporary filter.
    configure(
        "leaker", "router bgp 65066", "address-family ipv4 unicast",
        "neighbor 10.222.4.1 route-map NO-LEAK out",
    )
    vty("leaker", "clear bgp ipv4 unicast 10.222.4.1 soft out")
    configure(
        "validator", "router bgp 65002",
        "address-family ipv4 unicast",
        "neighbor 10.222.4.2 route-map CUSTOMER-IN in",
    )
    running = vty("validator", "show running-config")
    if "route-map SEC-LEAK-IN " in running:
        configure("validator", "no route-map SEC-LEAK-IN")
    running = vty("validator", "show running-config")
    if "bgp as-path access-list SEC-LEAK " in running:
        configure("validator", "no bgp as-path access-list SEC-LEAK")
    vty("validator", "clear bgp ipv4 unicast 10.222.4.2 soft in")

def best_path(node):
    data = json.loads(vty(
        node, "show bgp ipv4 unicast 192.0.2.0/24 json"
    ))
    best = [
        p for p in data.get("paths", [])
        if p.get("bestpath", {}).get("overall")
    ]
    if len(best) != 1:
        return None
    return best[0].get("aspath", {}).get("string")

def stage(name, leaked=False):
    expected = {
        "validator": (
            "65066 65003 65169" if leaked else "65001 65003 65169"
        ),
        "observer": (
            "65002 65066 65003 65169"
            if leaked else "65002 65001 65003 65169"
        ),
    }
    deadline = time.monotonic() + 45
    while True:
        observed = {node: best_path(node) for node in expected}
        if observed == expected:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(f"{name}: paths did not match: {observed}")
        time.sleep(1)
    ping = command("observer", [
        "ping", "-I", "198.51.100.1", "-c", "3", "-W", "2",
        "192.0.2.1",
    ])
    if not (
        "3 packets received" in ping or "3 received" in ping
    ):
        raise RuntimeError(f"{name}: incomplete ping delivery")
    record = {
        "stage": name, "status": "PASS",
        "expected": expected, "observed": observed,
        "ping": ping,
    }
    (OUT / f"{name}.json").write_text(json.dumps(record, indent=2))
    print(json.dumps({
        "stage": name, "status": "PASS", "paths": observed,
    }), flush=True)
    return record

manifest = {
    "scope": "Live FRR scenario-specific filter; not native ASPA/ROV",
    "prefix": "192.0.2.0/24",
    "expected_order": ["baseline", "leaked", "filtered", "restored"],
    "measurement": "Functional path checks and three pings per stage",
    "timing_note": "Polling deadlines are not convergence measurements",
}
(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
print("Evidence:", OUT, flush=True)

records = []
error = None
restoration = "NOT_ATTEMPTED"
try:
    # Require the clean baseline before any mutation.
    leaker = vty("leaker", "show running-config")
    validator = vty("validator", "show running-config")
    if "neighbor 10.222.4.1 route-map NO-LEAK out" not in leaker:
        raise RuntimeError("Export guard absent before test")
    if "neighbor 10.222.4.2 route-map CUSTOMER-IN in" not in validator:
        raise RuntimeError("Original import policy absent")
    if "SEC-LEAK" in validator:
        raise RuntimeError("Temporary filter already present")

    records.append(stage("baseline"))
    configure(
        "leaker", "router bgp 65066", "address-family ipv4 unicast",
        "no neighbor 10.222.4.1 route-map NO-LEAK out",
    )
    vty("leaker", "clear bgp ipv4 unicast 10.222.4.1 soft out")
    records.append(stage("leaked", leaked=True))

    configure(
        "validator",
        "bgp as-path access-list SEC-LEAK permit ^65066_65003_65169$",
        "route-map SEC-LEAK-IN deny 5",
        "match ip address prefix-list VICTIM",
        "match as-path SEC-LEAK", "exit",
        "route-map SEC-LEAK-IN permit 10",
        "set local-preference 200", "exit",
        "router bgp 65002", "address-family ipv4 unicast",
        "neighbor 10.222.4.2 route-map SEC-LEAK-IN in",
    )
    vty("validator", "clear bgp ipv4 unicast 10.222.4.2 soft in")
    records.append(stage("filtered"))
except Exception as exc:
    error = str(exc)
finally:
    try:
        restore()
        records.append(stage("restored"))
        leaker = vty("leaker", "show running-config")
        validator = vty("validator", "show running-config")
        if (
            "neighbor 10.222.4.1 route-map NO-LEAK out" not in leaker
            or "neighbor 10.222.4.2 route-map CUSTOMER-IN in" not in validator
            or "SEC-LEAK" in validator
        ):
            raise RuntimeError("Restored configuration check failed")
        restoration = "PASS"
    except Exception as exc:
        restoration = "FAIL"
        error = (error or "") + " Restoration: " + str(exc)

result = {
    "status": "PASS" if not error and len(records) == 4 else "FAIL",
    "stages_passed": len(records),
    "restoration": restoration,
    "error": error,
    "scope": manifest["scope"],
}
(OUT / "summary.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2), flush=True)
if result["status"] != "PASS":
    raise SystemExit(1)
