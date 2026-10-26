"""Offline feasibility pilot for multihomed accidental route leaks."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import importlib.metadata
import json
import random
import resource
import time

from frozendict import frozendict
from bgpy.as_graphs import CAIDAASGraphCollector
from bgpy.shared.enums import ASGroups
from bgpy.simulation_engine import ASPA, ROV, BGP, OnlyToCustomers
from bgpy.simulation_framework import (
    AccidentalRouteLeak, ScenarioConfig, Simulation,
)

ROOT = Path("/work")
data = ROOT / "datasets/security-asap/20240401.as-rel2.txt"
metadata = json.loads(
    (data.parent / "dataset-manifest.json").read_text()
)
digest = hashlib.sha256(data.read_bytes()).hexdigest()
if digest != metadata["text_sha256"]:
    raise RuntimeError("Dataset checksum mismatch")

out = ROOT / "results/22-security-asap" / datetime.now(
    timezone.utc
).strftime("%Y%m%dT%H%M%S%fZ-fig9-pilot")
out.mkdir(parents=True)

cache = ROOT / "datasets/security-asap/bgpy-cache"
collector = CAIDAASGraphCollector(
    dl_time=datetime(2024, 4, 5, 15, 3),
    cache_dir=cache,
)
cached = collector.cache_path
cached.parent.mkdir(parents=True, exist_ok=True)
if cached.exists():
    if hashlib.sha256(cached.read_bytes()).hexdigest() != digest:
        raise RuntimeError("Existing cache checksum mismatch")
else:
    cached.write_bytes(data.read_bytes())

configs = tuple(
    ScenarioConfig(
        ScenarioCls=AccidentalRouteLeak,
        BasePolicyCls=ROV,
        AdoptPolicyCls=policy,
        AttackerBasePolicyCls=BGP,
        attacker_subcategory_attr=ASGroups.MULTIHOMED.value,
        num_attackers=1,
        num_victims=1,
    )
    for policy in (ASPA, OnlyToCustomers)
)

plan = {
    "scope": "Feasibility pilot; not final Figure 9 reproduction",
    "dataset": metadata,
    "bgpy_version": importlib.metadata.version("bgpy_pkg"),
    "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "adoption": [0.5],
    "trials": 3,
    "workers": 1,
    "seed": 42,
    "python_hash_seed": 0,
    "base_policy": "ROV",
    "defenses": [c.AdoptPolicyCls.name for c in configs],
    "deviations": [
        "One adoption setting",
        "Three trials instead of 1000",
        "Two defenses",
        "Metric and sampling semantics require validation",
    ],
}
(out / "plan.json").write_text(json.dumps(plan, indent=2))
print("Evidence:", out, flush=True)
print("Dataset SHA256:", digest, flush=True)
print("Starting ASPA/OTC pilot: 3 trials at 50% adoption", flush=True)

random.seed(42)
start = time.monotonic()
try:
    sim = Simulation(
        sim_name="rono_fig9_mh_pilot",
        percent_adoptions=(0.5,),
        scenario_configs=configs,
        num_trials=3,
        parse_cpus=1,
        python_hash_seed=0,
        output_dir=out / "simulation",
        as_graph_constructor_kwargs=frozendict({
            "as_graph_collector_kwargs": frozendict({
                "cache_dir": cache,
                "dl_time": datetime(2024, 4, 5, 15, 3),
            }),
            "as_graph_kwargs": frozendict({
                "store_customer_cone_size": True,
                "store_customer_cone_asns": True,
                "store_provider_cone_size": True,
                "store_provider_cone_asns": True,
            }),
            "tsv_path": None,
        }),
    )
    sim.run()
    report = {
        "status": "PILOT_EXECUTION_COMPLETE",
        "elapsed_seconds": time.monotonic() - start,
        "process_max_rss_kib": resource.getrusage(
            resource.RUSAGE_SELF
        ).ru_maxrss,
        "scope": "Execution complete; comparison validation pending",
    }
except Exception as exc:
    report = {
        "status": "ERROR",
        "elapsed_seconds": time.monotonic() - start,
        "error": repr(exc),
    }
    (out / "summary.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)
    raise

(out / "summary.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2), flush=True)
print("CSV files:", [str(p) for p in out.rglob("*.csv")], flush=True)
print("Evidence:", out, flush=True)
