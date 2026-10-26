"""Run negative controls with an explicit temporary probe route."""
from datetime import datetime, timezone
from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
runner = ROOT / "projects/security/asap/live_lab/run_onos_negative_controls.py"
folder = ROOT / "results/22-security-asap" / datetime.now(
    timezone.utc
).strftime("%Y%m%dT%H%M%S%fZ-negative-control-routing")
folder.mkdir(parents=True)

prefix = "10.222.4.2/32"
route = [prefix, "via", "10.222.6.1", "dev", "eth1"]

def observer(*args):
    return subprocess.run(
        ["docker", "exec", "clab-secasap-observer", *args],
        capture_output=True, text=True, timeout=20,
    )

def save(name, data):
    (folder / name).write_text(json.dumps(data, indent=2))

existing = observer("ip", "-j", "route", "show", "exact", prefix)
if existing.returncode:
    raise SystemExit(existing.stderr)
if json.loads(existing.stdout):
    raise SystemExit("An exact probe route already exists; nothing changed.")

save("plan.json", {
    "purpose": "Provide baseline reachability for permitted traffic",
    "temporary_route": route,
    "scope": "Observer namespace only; no BGP advertisements changed",
    "cleanup": "Delete the exact route added by this wrapper",
})

added = False
error = None
cleanup = "NOT_REQUIRED"

try:
    result = observer("ip", "route", "add", *route)
    save("route-add.json", {
        "returncode": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
    })
    if result.returncode:
        raise RuntimeError("Route addition failed: " + result.stderr)
    added = True

    probe = observer(
        "ping", "-I", "198.51.100.1",
        "-c", "3", "-W", "2", "10.222.4.2",
    )
    save("baseline-control-ping.json", {
        "returncode": probe.returncode,
        "stdout": probe.stdout, "stderr": probe.stderr,
    })
    if probe.returncode:
        raise RuntimeError("Permitted-flow baseline failed; experiment not started.")
    print("PERMITTED_ROUTE_BASELINE_PASS", flush=True)

    result = subprocess.run(
        [sys.executable, str(runner)], cwd=ROOT,
        capture_output=True, text=True,
    )
    (folder / "experiment.log").write_text(
        result.stdout + "\n" + result.stderr
    )
    print(result.stdout, flush=True)
    if result.returncode:
        raise RuntimeError("Negative-control experiment failed; see experiment.log")

except BaseException as exc:
    error = str(exc) or type(exc).__name__
finally:
    if added:
        result = observer("ip", "route", "del", *route)
        save("route-delete.json", {
            "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
        })
        remaining = observer("ip", "-j", "route", "show", "exact", prefix)
        cleanup = (
            "PASS"
            if result.returncode == 0
            and remaining.returncode == 0
            and not json.loads(remaining.stdout)
            else "FAIL"
        )

report = {
    "status": "PASS" if error is None and cleanup == "PASS" else "FAIL",
    "error": error,
    "temporary_route_cleanup": cleanup,
    "scope": "Permitted-flow control with explicit temporary routing",
}
save("summary.json", report)
print(json.dumps(report, indent=2))
print("Wrapper evidence:", folder)
if report["status"] != "PASS":
    raise SystemExit(1)
