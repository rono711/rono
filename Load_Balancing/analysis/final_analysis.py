"""Reproducible descriptive analysis of RONO LB pilots."""

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "results/21-cn-load-balancing"
OUT = ROOT / "analysis/final_results"
OUT.mkdir(parents=True, exist_ok=True)

metrics = (
    "mean_controlled_mbps",
    "mean_satisfaction",
    "mean_reward",
)

records = []
runs = []

for folder in sorted(SOURCE.glob("*-evaluation")):
    config_file = folder / "config.json"
    summary_file = folder / "summary.json"

    if not config_file.exists() or not summary_file.exists():
        continue

    config = json.loads(config_file.read_text())
    report = json.loads(summary_file.read_text())

    if report.get("status") != "EVALUATION_PILOT_PASS":
        print("SKIPPED:", folder.name)
        continue

    horizon = config.get("horizon")
    methods = set()

    for item in report.get("summary", []):
        method = item.get("method")

        if not method or method in methods:
            raise ValueError(
                f"Duplicate or missing method: {folder.name}"
            )

        methods.add(method)

        if (
            item.get("attempted") != horizon
            or item.get("passed") != horizon
        ):
            raise ValueError(
                f"Incomplete trials: {folder.name}/{method}"
            )

        row = {
            "run": folder.name,
            "model_sha256": config.get("model_sha256", ""),
            "seed": config.get("seed"),
            "horizon": horizon,
            "method": method,
        }

        for metric in metrics:
            value = item.get(metric)
            if type(value) not in (int, float):
                raise ValueError(
                    f"Missing metric: {folder.name}/{method}/{metric}"
                )
            row[metric] = float(value)

        records.append(row)

    runs.append({
        "run": folder.name,
        "seed": config.get("seed"),
        "horizon": horizon,
        "model_sha256": config.get("model_sha256", ""),
        "methods": sorted(methods),
    })

assert records, "No valid evaluation records"

fields = list(records[0])

with (OUT / "evaluation_records.csv").open(
    "w", newline=""
) as f:
    writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)

# Keep the complete six-strategy comparisons separate.
required = {
    "fixed", "fixed_split", "ecmp",
    "least_utilized", "greedy_demand", "ppo"
}

eligible = {
    run["run"]
    for run in runs
    if run["horizon"] == 8
    and required.issubset(set(run["methods"]))
}

selected = [
    row for row in records
    if row["run"] in eligible
]

by_method = defaultdict(list)

for row in selected:
    by_method[row["method"]].append(row)

summary = []

for method, items in sorted(by_method.items()):
    result = {
        "method": method,
        "runs": len(items),
    }

    for metric in metrics:
        values = [x[metric] for x in items]
        result[metric] = statistics.mean(values)

    summary.append(result)

with (OUT / "six_strategy_summary.csv").open(
    "w", newline=""
) as f:
    if summary:
        writer = csv.DictWriter(
            f, fieldnames=list(summary[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(summary)

# Paired differences within each evaluation run.
paired = []
by_run = defaultdict(dict)

for row in selected:
    by_run[row["run"]][row["method"]] = row

for run, methods in sorted(by_run.items()):
    ppo = methods["ppo"]

    for method in sorted(required - {"ppo"}):
        baseline = methods[method]

        item = {
            "run": run,
            "baseline": method,
        }

        for metric in metrics:
            item[metric + "_difference"] = (
                ppo[metric] - baseline[metric]
            )

        paired.append(item)

if paired:
    with (OUT / "paired_differences.csv").open(
        "w", newline=""
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=list(paired[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(paired)

analysis = {
    "scope": "Descriptive pilot analysis only",
    "evaluation_runs": len(runs),
    "complete_six_strategy_runs": len(eligible),
    "run_metadata": runs,
    "six_strategy_summary": summary,
    "limitations": [
        "Runs may reuse models or seeds.",
        "Workloads and scenario distributions may differ.",
        "Run means are not independent scenario samples.",
        "No statistical superiority claim.",
        "ECMP uses limited fixed flow tuples.",
    ],
}

(OUT / "analysis.json").write_text(
    json.dumps(analysis, indent=2) + "\n"
)

print("===== RONO LOAD BALANCING ANALYSIS =====")
print("Evaluation runs:", len(runs))
print("Complete six-strategy runs:", len(eligible))

for item in sorted(
    summary,
    key=lambda x: -x["mean_controlled_mbps"]
):
    print(
        f'{item["method"]:18s}',
        f'{item["mean_controlled_mbps"]:.3f} Mbps',
        f'Satisfaction={item["mean_satisfaction"]:.3f}',
        f'Reward={item["mean_reward"]:.3f}'
    )

# Generate three figures when matplotlib is available.
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for metric, title, filename in (
        ("mean_controlled_mbps",
         "Controlled Throughput (Mbps)",
         "throughput.png"),
        ("mean_satisfaction",
         "Demand Satisfaction",
         "satisfaction.png"),
        ("mean_reward",
         "Mean Reward",
         "reward.png"),
    ):
        if not summary:
            continue

        names = [x["method"] for x in summary]
        values = [x[metric] for x in summary]

        fig, ax = plt.subplots(figsize=(9, 4.8))
        ax.bar(names, values)
        ax.set_ylabel(title)
        ax.set_title(
            "RONO: Complete Six-Strategy Pilots"
        )
        ax.tick_params(axis="x", rotation=25)
        ax.grid(axis="y", alpha=0.2)
        fig.tight_layout()
        fig.savefig(OUT / filename, dpi=220)
        plt.close(fig)

except ImportError:
    print("Matplotlib unavailable; CSV files saved.")

print("\nResults saved:", OUT)
print("Analysis completed.")
