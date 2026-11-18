import csv
import json
import math
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "DDS/benchmark/qos_runner.py"

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
campaign = ROOT / "DDS/results/qos-benchmark/campaigns" / (
    stamp + "-" + uuid.uuid4().hex[:8]
)
campaign.mkdir(parents=True)

def percentile(values, p):
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * p / 100
    lo = math.floor(position)
    hi = math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)

def load_events(path, event):
    records = []
    for line in path.read_text().splitlines():
        try:
            item = json.loads(line)
            if item.get("event") == event:
                records.append(item)
        except json.JSONDecodeError:
            pass
    return records

rows = []

try:
    for trial in range(1, 6):
        for mode, domain in (
            ("reliable", 78),
            ("best_effort", 79),
        ):
            print(f"Running {mode} trial {trial}/5", flush=True)

            result = subprocess.run(
                [
                    sys.executable, str(RUNNER),
                    "--reliability", mode,
                    "--samples", "100",
                    "--domain", str(domain),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=180,
            )

            if result.returncode:
                raise RuntimeError(
                    f"{mode} trial {trial} failed:\n"
                    + result.stderr[-3000:]
                )

            summary = json.loads(result.stdout)
            folder = Path(summary["result_directory"])

            matches = load_events(
                folder / "publisher.log", "endpoint_matching"
            )
            if not matches or not matches[-1].get("matched"):
                raise RuntimeError("Endpoint matching not confirmed")

            received = load_events(
                folder / "subscriber.log", "sample_received"
            )

            latencies = [
                x["application_latency_ns"] / 1e6
                for x in received
                if x.get("application_latency_ns") is not None
                and x["application_latency_ns"] >= 0
            ]

            row = {
                "trial": trial,
                "mode": mode,
                "domain": domain,
                "requested": summary["requested_samples"],
                "written": summary["successful_writes"],
                "received": summary["unique_received"],
                "delivery_ratio": summary["observed_delivery_ratio"],
                "mean_ms": summary["mean_application_latency_ms"],
                "median_ms": percentile(latencies, 50),
                "p95_ms": percentile(latencies, 95),
                "p99_ms": percentile(latencies, 99),
                "matching_ms": matches[-1]["matching_time_ms"],
                "result_directory": str(folder.relative_to(ROOT)),
            }

            rows.append(row)

            print(
                f"  Received: {row['received']}/100 | "
                f"Mean: {row['mean_ms']:.3f} ms",
                flush=True,
            )

finally:
    (campaign / "results.json").write_text(
        json.dumps(rows, indent=2) + "\n"
    )

    if rows:
        with (campaign / "results.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

print("\nCampaign results:", campaign)
print("Completed trials:", len(rows))
