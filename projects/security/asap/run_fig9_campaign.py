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
).strftime("%Y%m%dT%H%M%S%fZ-fig9-campaign")
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


from bgpy.shared.enums import Plane, Outcomes
from bgpy.simulation_framework.graph_data_aggregator import GraphDataAggregator

class AuditedAggregator(GraphDataAggregator):
    def aggregate_and_store_trial_data(self, **kwargs):
        engine = kwargs["engine"]
        scenario = kwargs["scenario"]
        if kwargs["propagation_round"] == 1:
            excluded = scenario.untracked_asns
            group = engine.as_graph.asn_groups["all_wout_ixps"]
            eligible = [
                a.asn for a in engine.as_graph
                if a.asn in group and a.asn not in excluded
            ]
            if not eligible:
                raise RuntimeError("No eligible ASes in metric denominator")
            outcomes = kwargs["outcomes"][Plane.DATA.value]
            missing = [n for n in eligible if n not in outcomes]
            if missing:
                raise RuntimeError("Missing data-plane outcomes")
            attracted = sum(
                outcomes[n] == Outcomes.ATTACKER_SUCCESS.value
                for n in eligible
            )
            adopters = sorted(scenario.adopting_asns)
            row = {
                "trial": kwargs["trial"],
                "adoption": float(kwargs["percent_adopt"]),
                "policy": scenario.scenario_config.AdoptPolicyCls.__name__,
                "attackers": sorted(scenario.attacker_asns),
                "victims": sorted(scenario.victim_asns),
                "adopting_asns_sha256": hashlib.sha256(
                    json.dumps(adopters).encode()
                ).hexdigest(),
                "adopting_count": len(adopters),
                "eligible_as_count": len(eligible),
                "attracted_as_count": attracted,
                "attraction_percent": 100 * attracted / len(eligible),
            }
            with (out / "trial-audit.jsonl").open("a") as f:
                f.write(json.dumps(row) + "\\n")
        return super().aggregate_and_store_trial_data(**kwargs)

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
    "scope": "Reduced-trial Figure 9(a) reproduction campaign",
    "dataset": metadata,
    "bgpy_version": importlib.metadata.version("bgpy_pkg"),
    "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "adoption": [0.1, 0.2, 0.5, 0.8, 0.99],
    "trials": 100,
    "workers": 1,
    "outer_seed": 42,
    "python_hash_seed": 0,
    "base_policy": "ROV",
    "defenses": [c.AdoptPolicyCls.name for c in configs],
    "deviations": [
        "Five adoption settings; verify correspondence with published points",
        "100 trials per setting instead of 1000",
        "Two defenses",
        "Sampling audited; upstream yerr is retained without relabelling",
    ],
}
(out / "plan.json").write_text(json.dumps(plan, indent=2))
print("Evidence:", out, flush=True)
print("Dataset SHA256:", digest, flush=True)
print("Starting ASPA/OTC campaign: 100 trials at five adoption settings", flush=True)

random.seed(42)
start = time.monotonic()
try:
    sim = Simulation(
        sim_name="rono_fig9_mh_campaign",
        percent_adoptions=(0.1, 0.2, 0.5, 0.8, 0.99),
        scenario_configs=configs,
        num_trials=100,
        parse_cpus=1,
        python_hash_seed=0,
        output_dir=out / "simulation",
        GraphDataAggregatorCls=AuditedAggregator,
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
        "status": "CAMPAIGN_EXECUTION_COMPLETE",
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
