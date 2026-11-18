import argparse
import csv
import fcntl
import json
import math
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LAB = ROOT / "Shared_Infrastructure/onos/shared_lab.py"
OUT_ROOT = ROOT / "Shared_Infrastructure/results/21-onos-benchmark"

FIELDS = [
    "cycle", "action", "status",
    "command_ms", "verification_ms", "total_ms",
    "error"
]


def run(command, timeout=240):
    start = time.monotonic_ns()
    try:
        result = subprocess.run(
            [sys.executable, str(LAB), command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed = (time.monotonic_ns() - start) / 1e6
        return result.returncode, elapsed, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        elapsed = (time.monotonic_ns() - start) / 1e6
        return 124, elapsed, str(exc.stdout or ""), str(exc.stderr or "")


def main(cycles):
    folder = OUT_ROOT / datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    folder.mkdir(parents=True, exist_ok=False)

    rows = []
    error = ""
    restoration = "NOT_ATTEMPTED"

    def invoke(command, label):
        code, elapsed, stdout, stderr = run(command)
        (folder / (label + ".log")).write_text(
            stdout + "\n" + stderr
        )
        if code:
            raise RuntimeError(
                f"{command} failed with exit code {code}"
            )

        if command.startswith(("verify-", "dds-")):
            decoder = json.JSONDecoder()
            payload = None

            for index, char in enumerate(stdout):
                if char != "{":
                    continue
                try:
                    candidate, _ = decoder.raw_decode(stdout[index:])
                except ValueError:
                    continue

                if isinstance(candidate, dict):
                    payload = candidate
                    break

            if payload is None:
                raise RuntimeError(
                    f"{command}: no valid JSON object found"
                )

            if not isinstance(payload, dict):
                raise RuntimeError(
                    f"{command}: expected JSON object"
                )

            if payload.get("status") != "PASS":
                raise RuntimeError(
                    f"{command}: PASS not confirmed"
                )

        return elapsed

    def verify(expected, label):
        return invoke("verify-" + expected, label)

    try:
        verify("connected", "initial-connected")

        for cycle in range(1, cycles + 1):
            for action, before, after in (
                ("disconnect", "connected", "disconnected"),
                ("connect", "disconnected", "connected"),
            ):
                row = {
                    "cycle": cycle,
                    "action": action,
                    "status": "FAIL",
                    "command_ms": "",
                    "verification_ms": "",
                    "total_ms": "",
                    "error": "",
                }

                try:
                    verify(before, f"{cycle}-{action}-precheck")

                    command_ms = invoke(
                        "dds-" + action,
                        f"{cycle}-{action}-command"
                    )
                    row["command_ms"] = round(command_ms, 3)

                    verify_ms = verify(
                        after,
                        f"{cycle}-{action}-verification"
                    )
                    row["verification_ms"] = round(verify_ms, 3)
                    row["total_ms"] = round(
                        command_ms + verify_ms, 3
                    )
                    row["status"] = "PASS"

                except Exception as exc:
                    row["error"] = str(exc)
                    raise

                finally:
                    rows.append(row)
                    with (folder / "trials.csv").open(
                        "w", newline=""
                    ) as f:
                        writer = csv.DictWriter(
                            f, fieldnames=FIELDS
                        )
                        writer.writeheader()
                        writer.writerows(rows)

                print(
                    f"Cycle {cycle}/{cycles}: "
                    f"{action} {row['status']}",
                    flush=True,
                )

    except Exception as exc:
        error = str(exc)

    finally:
        try:
            invoke("connect", "restoration-connect")
            verify("connected", "restoration-verify")
            restoration = "PASS"
        except Exception as exc:
            restoration = "FAIL"
            error += " | Restoration: " + str(exc)

        summary = []

        for action in ("disconnect", "connect"):
            group = [
                r for r in rows
                if r["action"] == action
                and r["status"] == "PASS"
            ]
            values = sorted(
                float(r["total_ms"]) for r in group
            )

            summary.append({
                "action": action,
                "passed": len(group),
                "median_ms": (
                    statistics.median(values)
                    if values else None
                ),
                "p95_ms": (
                    values[math.ceil(.95 * len(values)) - 1]
                    if values else None
                ),
            })

        complete = (
            len(rows) == cycles * 2
            and all(r["status"] == "PASS" for r in rows)
        )

        result = {
            "complete": complete,
            "cycles_requested": cycles,
            "transitions_recorded": len(rows),
            "passed": sum(
                r["status"] == "PASS" for r in rows
            ),
            "restoration": restoration,
            "error": error,
            "summary": summary,
            "metric": (
                "CLI DDS command duration plus subsequent "
                "BGP/data-plane verification duration"
            ),
            "limitations": (
                "Includes Docker sender startup, DDS discovery, "
                "two-second sender hold and polling. "
                "Not pure DDS or ONOS latency."
            ),
        }

        (folder / "summary.json").write_text(
            json.dumps(result, indent=2)
        )

        print(json.dumps(result, indent=2))
        print("Evidence:", folder)

    complete = (
        len(rows) == cycles * 2
        and all(r["status"] == "PASS" for r in rows)
    )

    if not complete:
        error = error or "Incomplete benchmark campaign"

    return 0 if complete and not error and restoration == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cycles", type=int, choices=(5, 30), default=5
    )
    args = parser.parse_args()

    lock_path = Path.home() / ".cache/rono/onos-campaign.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w") as lock:
        try:
            fcntl.flock(
                lock, fcntl.LOCK_EX | fcntl.LOCK_NB
            )
        except BlockingIOError:
            sys.exit("Another ONOS campaign is running")

        sys.exit(main(args.cycles))
