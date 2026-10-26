"""Five sequential ONOS/FRR cycles; stop on first failure."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[4]
runner = root / "projects/security/asap/live_lab/run_onos_recovery.py"
if not runner.is_file():
    raise SystemExit("Recovery runner missing; complete the saving step first.")

base = root / "results/22-security-asap"
folder = base / datetime.now(timezone.utc).strftime(
    "%Y%m%dT%H%M%S%fZ-onos-recovery-repeatability"
)
folder.mkdir(parents=True)

plan = {
    "planned": 5,
    "execution": "Sequential; stop on first failure",
    "runner_sha256": hashlib.sha256(runner.read_bytes()).hexdigest(),
    "scope": "Same-host functional repeatability; local authorization data",
}
(folder / "plan.json").write_text(json.dumps(plan, indent=2))
records = []

for cycle in range(1, 6):
    before = set(base.glob("*-onos-frr-recovery"))
    result = subprocess.run(
        [sys.executable, str(runner)],
        cwd=root, capture_output=True, text=True,
    )
    (folder / f"cycle-{cycle}.log").write_text(
        result.stdout + "\n" + result.stderr
    )

    created = set(base.glob("*-onos-frr-recovery")) - before
    summary = {}
    evidence = None
    if len(created) == 1:
        evidence = created.pop()
        summary_file = evidence / "summary.json"
        if summary_file.exists():
            summary = json.loads(summary_file.read_text())

    passed = (
        result.returncode == 0
        and summary.get("status") == "PASS"
        and summary.get("restoration") == "PASS"
        and not summary.get("cleanup_errors")
    )
    records.append({
        "cycle": cycle,
        "status": "PASS" if passed else "FAIL",
        "returncode": result.returncode,
        "evidence": str(evidence) if evidence else None,
        "summary": summary,
    })
    (folder / "progress.json").write_text(json.dumps(records, indent=2))
    print(f"Cycle {cycle}: {'PASS' if passed else 'FAIL'}", flush=True)
    if not passed:
        print(result.stdout + result.stderr, flush=True)
        break

passed_count = sum(r["status"] == "PASS" for r in records)
report = {
    "status": "PASS" if passed_count == 5 else "FAIL",
    "planned": 5,
    "attempted": len(records),
    "passed": passed_count,
    "records": records,
    "scope": plan["scope"],
}
(folder / "summary.json").write_text(json.dumps(report, indent=2))
print(json.dumps({
    k: report[k] for k in ("status", "planned", "attempted", "passed")
}, indent=2))
print("Campaign evidence:", folder)
if report["status"] != "PASS":
    raise SystemExit(1)
