"""Offline deterministic BGP security checks using pinned ASPA/BGPy code."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import importlib
import importlib.metadata
import json

from frozendict import frozendict
from bgpy.as_graphs.base.links import CustomerProviderLink as CP
from bgpy.simulation_engine import BGP, ROV, ASPA
from bgpy.simulation_framework import AccidentalRouteLeak
from bgpy.tests.engine_tests.utils import EngineTester

OUT = Path("results/22-security-asap") / datetime.now(
    timezone.utc
).strftime("%Y%m%dT%H%M%S%fZ-replay")
OUT.mkdir(parents=True)

def load(name):
    module = importlib.import_module(
        "aspa_eval.tests.engine_tests.engine_test_configs." + name
    )
    return getattr(module, name)

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
                raise RuntimeError(f"Unexpected pre-leak paths: {paths}")
        return super().post_propagation_hook(
            engine=engine, percent_adopt=percent_adopt,
            trial=trial, propagation_round=propagation_round,
        )

class GuardedRunner(EngineTester):
    def _get_engine_and_scenario(self):
        engine, scenario = super()._get_engine_and_scenario()
        actual = {
            n: type(a.policy) for n, a in engine.as_graph.as_dict.items()
        }
        if actual != self.expected_policies:
            raise RuntimeError("Policy mismatch before propagation: " + str(
                {n: c.__name__ for n, c in actual.items()}
            ))
        return engine, scenario

cases = []
definitions = [
    ("google_hijack", BGP, BGP, 0, 0),
    ("google_hijack_rov", ROV, ROV, 1, 1),
    ("google_origin_hijack", ROV, ROV, 0, 1),
    ("google_origin_hijack_aspa", ASPA, ASPA, 1, 1),
]

def add_case(name, config, policies, expected, paths=None):
    cases.append({
        "name": name, "config": config, "policies": policies,
        "expected": expected, "expected_paths": paths or {},
    })

for observer in (False, True):
    for name, validator, victim, outcome2, outcome1 in definitions:
        original = load(name)
        graph = original.as_graph_info
        policies = {1: BGP, 2: validator, 3: BGP,
                    666: BGP, 15169: victim}
        expected = {1: outcome1, 2: outcome2, 3: 1,
                    666: 0, 15169: 1}
        label = "rono_" + name
        if observer:
            label += "_observer"
            graph = replace(
                graph, customer_provider_links=(
                    graph.customer_provider_links |
                    frozenset({CP(provider_asn=2, customer_asn=65004)})
                ),
            )
            policies[65004] = BGP
            expected[65004] = outcome2
        config = replace(
            original, name=label, as_graph_info=graph,
            scenario_config=replace(
                original.scenario_config,
                AdoptPolicyCls=victim,
                override_adopting_asns=frozenset(),
            ),
        )
        add_case(label, config, policies, expected)

original = load("google_origin_hijack_aspa")
observer_graph = replace(
    original.as_graph_info,
    customer_provider_links=(
        original.as_graph_info.customer_provider_links |
        frozenset({CP(provider_asn=2, customer_asn=65004)})
    ),
)
for label, victim_policy, outcome in [
    ("rono_record_present", ASPA, 1),
    ("rono_record_absent", BGP, 0),
]:
    policies = {1: BGP, 2: ASPA, 3: BGP, 666: BGP,
                15169: victim_policy, 65004: BGP}
    config = replace(
        original, name=label, as_graph_info=observer_graph,
        scenario_config=replace(
            original.scenario_config,
            AdoptPolicyCls=victim_policy,
            override_adopting_asns=frozenset(),
            hardcoded_asn_cls_dict=frozendict({
                2: ASPA, 15169: victim_policy,
            }),
        ),
    )
    expected = {1: 1, 2: outcome, 3: 1, 666: 0,
                15169: 1, 65004: outcome}
    add_case(label, config, policies, expected)

original = load("google_hijack")
leak_graph = replace(
    observer_graph,
    customer_provider_links=(
        observer_graph.customer_provider_links |
        frozenset({CP(provider_asn=3, customer_asn=666)})
    ),
)
for label, policy in [("bgp", BGP), ("rov", ROV), ("aspa", ASPA)]:
    protected = policy is ASPA
    path2 = [2, 1, 3, 15169] if protected else [2, 666, 3, 15169]
    policies = {1: BGP, 2: policy, 3: policy, 666: BGP,
                15169: policy, 65004: BGP}
    config = replace(
        original, name="rono_leak_" + label, as_graph_info=leak_graph,
        scenario_config=replace(
            original.scenario_config,
            ScenarioCls=ObservedLeak, propagation_rounds=2,
            AdoptPolicyCls=policy,
            override_adopting_asns=frozenset(),
            hardcoded_asn_cls_dict=frozendict({
                2: policy, 3: policy, 15169: policy,
            }),
        ),
    )
    expected = {1: 1, 2: int(protected), 3: 1,
                666: 0, 15169: 1, 65004: int(protected)}
    add_case("rono_leak_" + label, config, policies, expected, {
        "2": [path2],
        "65004": [[65004] + path2],
        "3": [[3, 15169]],
        "15169": [[15169]],
    })

manifest = {
    "bgpy_version": importlib.metadata.version("bgpy_pkg"),
    "scope": "Regression replay; not independent Internet-scale reproduction",
    "cases": [{
        "name": c["name"],
        "expected_policies": {
            n: p.__name__ for n, p in c["policies"].items()
        },
        "expected_outcomes": c["expected"],
        "expected_paths": c["expected_paths"],
    } for c in cases],
}
(OUT / "expected-before-run.json").write_text(
    json.dumps(manifest, indent=2)
)
print("Evidence:", OUT.resolve(), flush=True)
records = []
try:
    for case in cases:
        runner = GuardedRunner(
            base_dir=OUT, conf=case["config"],
            overwrite=False, dpi=96,
        )
        runner.expected_policies = case["policies"]
        engine, outcomes, _, scenario = runner.run_engine()
        outcomes = {int(n): int(v) for n, v in outcomes.items()}
        paths = {
            str(n): [list(a.as_path)
                     for _, a in obj.policy.local_rib.items()]
            for n, obj in engine.as_graph.as_dict.items()
        }
        passed = outcomes == case["expected"] and all(
            paths[n] == wanted
            for n, wanted in case["expected_paths"].items()
        )
        record = {
            "name": case["name"],
            "status": "PASS" if passed else "FAIL",
            "outcomes": outcomes,
            "selected_paths": paths,
        }
        records.append(record)
        (OUT / (case["name"] + ".json")).write_text(
            json.dumps(record, indent=2)
        )
        print(case["name"], record["status"], flush=True)
    passed = all(r["status"] == "PASS" for r in records)
    (OUT / "summary.json").write_text(json.dumps({
        "status": "PASS" if passed else "FAIL",
        "attempted": len(records),
        "passed": sum(r["status"] == "PASS" for r in records),
        "records": records,
    }, indent=2))
    if not passed:
        raise SystemExit(1)
except Exception as exc:
    (OUT / "error.json").write_text(json.dumps({
        "error": str(exc), "completed": len(records),
    }, indent=2))
    raise
