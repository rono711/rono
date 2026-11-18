import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "DDS/benchmark/policy_benchmark.py"
IMAGE = "rono/policy-type:0.1"

parser = argparse.ArgumentParser()
parser.add_argument("--reliability",
                    choices=["reliable", "best_effort"],
                    default="reliable")
parser.add_argument("--samples", type=int, default=10)
parser.add_argument("--domain", type=int, default=78)
args = parser.parse_args()

if not 1 <= args.samples <= 100:
    parser.error("samples must be between 1 and 100")
if not 0 <= args.domain <= 200:
    parser.error("domain must be between 0 and 200")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
out = ROOT / "DDS/results/qos-benchmark" / (
    f"{stamp}-{args.reliability}"
)
out.mkdir(parents=True, exist_ok=False)

prefix = f"rono-qos-{args.domain}"
sub_name = prefix + "-sub"
pub_name = prefix + "-pub"

def docker(*command, check=True):
    return subprocess.run(
        ["docker", *command],
        text=True,
        capture_output=True,
        check=check,
        timeout=180,
    )

def launch(name, role, extra):
    command = (
        "source /opt/fastdds-python/install/setup.bash; "
        'export PYTHONPATH="/opt/rono/policy/build:${PYTHONPATH:-}"; '
        "exec /usr/bin/python3 -u /opt/rono/policy_benchmark.py "
        + role + " --domain " + str(args.domain)
        + " --reliability " + args.reliability
        + " --durability volatile --history-depth 100 "
        + extra
    )
    docker(
        "run", "-d", "--name", name,
        "--network", "host", "--ipc", "host",
        "-v", f"{APP}:/opt/rono/policy_benchmark.py:ro",
        IMAGE, "bash", "-lc", command,
    )

def events(log, kind):
    records = []
    for line in log.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("event") == kind:
            records.append(item)
    return records

try:
    launch(sub_name, "subscriber", "")

    ready = False
    for _ in range(30):
        logs = docker("logs", sub_name).stdout
        if "Policy subscriber ready" in logs:
            ready = True
            break
        time.sleep(0.2)

    if not ready:
        raise RuntimeError("Subscriber did not become ready")

    # Allow DDS discovery to settle before publishing.
    time.sleep(3)

    launch(
        pub_name,
        "publisher",
        f"--samples {args.samples} --interval 0.01 "
        "--version 1 --hold-seconds 10",
    )

    exit_code = int(docker("wait", pub_name).stdout.strip())
    if exit_code:
        raise RuntimeError(f"Publisher exited with {exit_code}")

    time.sleep(2)

    pub_log = docker("logs", pub_name).stdout
    sub_log = docker("logs", sub_name).stdout

    (out / "publisher.log").write_text(pub_log)
    (out / "subscriber.log").write_text(sub_log)

    writes = events(pub_log, "write_return")
    received = events(sub_log, "sample_received")

    sent_versions = {
        x["version"] for x in writes
        if str(x.get("result")) == "0"
    }
    received_versions = {
        x["version"] for x in received
    }

    matched = sent_versions & received_versions
    latencies = [
        x["application_latency_ns"] / 1_000_000
        for x in received
        if x["version"] in matched
        and x["application_latency_ns"] >= 0
    ]

    summary = {
        "timestamp_utc": stamp,
        "reliability": args.reliability,
        "domain": args.domain,
        "requested_samples": args.samples,
        "successful_writes": len(sent_versions),
        "raw_receive_events": len(received),
        "unique_received": len(received_versions),
        "matched_versions": len(matched),
        "observed_delivery_ratio":
            len(matched) / len(sent_versions)
            if sent_versions else None,
        "mean_application_latency_ms":
            sum(latencies) / len(latencies)
            if latencies else None,
        "latency_sample_count": len(latencies),
        "result_directory": str(out),
    }

    (out / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )

    print(json.dumps(summary, indent=2))

finally:
    for name in (pub_name, sub_name):
        docker("rm", "-f", name, check=False)
