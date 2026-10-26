"""Deterministic deployment ablation; no Internet-scale success-rate claim."""
from dataclasses import replace
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
import csv
import hashlib
import importlib.metadata
import json

from frozendict import frozendict
from bgpy.as_graphs.base.links import CustomerProviderLink as CP
from bgpy.simulation_engine import BGP, ROV, ASPA
from bgpy.simulation_framework import AccidentalRouteLeak
from bgpy.tests.engine_tests.utils import EngineTester
from aspa_eval.tests.engine_tests.engine_test_configs.google_hijack import (
    google_hijack as original,
)

# Use the actual valid-prefix parent of the installed leak implementation.
ValidPrefixScenario = AccidentalRouteLeak.__bases__[0]
if ValidPrefixScenario.__name__ != "VictimsPrefix":
    raise RuntimeError("Unexpected scenario inheritance; stop for inspection.")

OUT = Path("results/22-security-asap") / datetime.now(
    timezone.utc
).strftime("%Y%m%dT%H%M%S%fZ-partial-deployment")
OUT.mkdir(parents=True)

def save(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2))

class ObservedLeak(AccidentalRouteLeak):
    def post_propagation_hook(
        self, engine, percent_adopt, trial, propagation_round
    ):
        if propagation_round == 0:
            paths = [
                list(a.as_path)
                for _, a in engine.as_graph.as_dict[
                    666
                ].policy.local_rib.items()
            ]
            if paths != [[666, 3, 15169]]:
                raise RuntimeError("Unexpected pre-leak path: " + str(paths))
        return super().post_propagation_hook(
            engine=engine, percent_adopt=percent_adopt,
            trial=trial, propagation_round=propagation_round,
        )

class GuardedRunner(EngineTester):
    def _get_engine_and_scenario(self):
        engine, scenario = super()._get_engine_and_scenario()
        actual = {
            n: type(a.policy)
            for n, a in engine.as_graph.as_dict.items()
        }
        if actual != self.expected_policies:
            raise RuntimeError("Effective policy assignment mismatch")
        return engine, scenario

graph = replace(
    original.as_graph_info,
    customer_provider_links=(
        original.as_graph_info.customer_provider_links
        | frozenset({
            CP(provider_asn=2, customer_asn=65004),
            CP(provider_asn=3, customer_asn=666),
        })
    ),
)

cases = []

# Eight ASPA deployment subsets. Non-adopters use ROV.
deployments = [
    (
        f"aspa_{v}{p}{o}",
        {2: ASPA if v else ROV,
         3: ASPA if p else ROV,
         15169: ASPA if o else ROV},
        bool(v and p),
    )
    for v, p, o in product((0, 1), repeat=3)
]
# Separate ordinary BGP reference; the all-ROV reference is aspa_000.
deployments.insert(
    0, ("bgp_reference", {2: BGP, 3: BGP, 15169: BGP}, False)
)

for label, selected, protected in deployments:
    background = BGP if label == "bgp_reference" else ROV
    policies = {
        1: background, 2: selected[2], 3: selected[3],
        666: BGP, 15169: selected[15169], 65004: background,
    }
    for leak in (False, True):
        name = "rono_partial_" + label + ("_leak" if leak else "_control")
        # Hypothesis fixed before execution:
        # rejecting the tested down-up leak requires validator ASPA
        # and provider AS3's modeled authorization record.
        attracted = bool(leak and not protected)
        path2 = (
            [2, 666, 3, 15169] if attracted
            else [2, 1, 3, 15169]
        )
        conf = replace(
            original,
            name=name,
            as_graph_info=graph,
            scenario_config=replace(
                original.scenario_config,
                ScenarioCls=ObservedLeak if leak else ValidPrefixScenario,
                propagation_rounds=2 if leak else 1,
                BasePolicyCls=background,
                AdoptPolicyCls=policies[15169],
                AttackerBasePolicyCls=BGP,
                override_adopting_asns=frozenset(),
                override_attacker_asns=frozenset({666}),
                override_victim_asns=frozenset({15169}),
                hardcoded_asn_cls_dict=frozendict(policies),
            ),
        )
        cases.append({
            "name": name, "deployment": label, "leak": leak,
            "policies": policies, "config": conf,
            "expected_attraction": attracted,
            "expected_paths": {
                "2": [path2],
                "65004": [[65004] + path2],
                "3": [[3, 15169]],
                "15169": [[15169]],
            },
        })

save("expected-before-run.json", {
    "scope": "Deterministic mechanism/deployment ablation",
    "bgpy_version": importlib.metadata.version("bgpy_pkg"),
    "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "bit_order": ["validator_AS2", "provider_AS3", "victim_AS15169"],
    "important_limit": (
        "ASPA policy deployment couples verification and modeled "
        "record publication; these factors are not independently varied."
    ),
    "cases": [{
        "name": c["name"],
        "deployment": c["deployment"],
        "leak": c["leak"],
        "expected_policies": {
            str(n): p.__name__ for n, p in c["policies"].items()
        },
        "expected_paths": c["expected_paths"],
        "expected_observer_attraction": c["expected_attraction"],
    } for c in cases],
})
print("Evidence:", OUT.resolve(), flush=True)

records = []
error = None
try:
    for case in cases:
        runner = GuardedRunner(
            base_dir=OUT, conf=case["config"],
            overwrite=False, dpi=96,
        )
        runner.expected_policies = case["policies"]
        engine, outcomes, _, scenario = runner.run_engine()
        paths = {
            str(n): [list(a.as_path)
                     for _, a in obj.policy.local_rib.items()]
            for n, obj in engine.as_graph.as_dict.items()
        }

        expected = case["expected_paths"]
        path_checks = {
            n: paths.get(n) == wanted for n, wanted in expected.items()
        }
        observer_paths = paths.get("65004", [])
        attracted = any(666 in p for p in observer_paths)
        reachable = bool(observer_paths) and all(
            p[-1] == 15169 for p in observer_paths
        )
        passed = (
            all(path_checks.values())
            and reachable
            and attracted == case["expected_attraction"]
        )
        record = {
            "name": case["name"],
            "deployment": case["deployment"],
            "leak": case["leak"],
            "status": "PASS" if passed else "FAIL",
            "actual_policies": {
                str(n): type(a.policy).__name__
                for n, a in engine.as_graph.as_dict.items()
            },
            "selected_paths": paths,
            "path_checks": path_checks,
            "observer_attracted": attracted,
            "selected_path_ends_at_victim": reachable,
            "simulator_outcomes": {
                str(n): int(v) for n, v in outcomes.items()
            },
        }
        records.append(record)
        save(case["name"] + ".json", record)
        print(case["name"], record["status"], flush=True)
        if not passed:
            raise RuntimeError(
                "Prediction mismatch; preserved for diagnosis. "
                "Do not revise expectations merely to obtain PASS."
            )
except Exception as exc:
    error = str(exc)
    save("error.json", {"error": error, "completed": len(records)})
finally:
    report = {
        "status": "PASS" if error is None and len(records) == len(cases) else "FAIL",
        "planned": len(cases),
        "completed": len(records),
        "passed": sum(r["status"] == "PASS" for r in records),
        "error": error,
        "scope": "Small-graph ablation; not Internet-scale effectiveness",
        "records": records,
    }
    save("summary.json", report)
    with (OUT / "comparison.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "deployment", "leak", "status", "observer_attracted",
            "selected_path_ends_at_victim", "observer_paths",
        ])
        for r in records:
            writer.writerow([
                r["deployment"], r["leak"], r["status"],
                r["observer_attracted"],
                r["selected_path_ends_at_victim"],
                json.dumps(r["selected_paths"].get("65004", [])),
            ])
    print(json.dumps({
        k: report[k]
        for k in ("status", "planned", "completed", "passed", "error")
    }, indent=2))
    print("Evidence:", OUT.resolve())

if report["status"] != "PASS":
    raise SystemExit(1)
