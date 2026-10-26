"""Matched congestion-shift pilot; frozen PPO, live episodic measurements."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import time

import torch
from stable_baselines3 import PPO
from rl_env import LiveEnv
from rl_core import ACTIONS
from rl_evaluate import baseline

# Two decisions in each phase. Context changes; telemetry is from prior trial.
PHASES = [
    ("A_low_path0", 0, 4),
    ("B_high_path0", 0, 16),
    ("C_high_path1", 1, 16),
    ("D_low_path1", 1, 4),
]
SEQUENCE = [
    (name, path, load)
    for name, path, load in PHASES
    for _ in range(2)
]
METHODS = [
    "fixed", "fixed_split", "ecmp",
    "least_utilized", "greedy_demand", "ppo",
]


class SequenceEnv(LiveEnv):
    def reset(self, *, seed=None, options=None):
        self.context_index = 0
        return super().reset(seed=seed, options=options)

    def context(self):
        _, path, load = SEQUENCE[self.context_index]
        self.demands = [9, 9]
        self.bg_path = path
        self.bg_mbps = load
        self.context_index += 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261026)
    args = parser.parse_args()
    torch.set_num_threads(1)
    model = PPO.load(args.model, device="cpu")
    model.policy.set_training_mode(False)

    folder = Path("results/21-cn-load-balancing") / datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%S%fZ-adaptation")
    folder.mkdir(parents=True)
    order = list(METHODS)
    random.Random(args.seed).shuffle(order)
    plan = {
        "scope": "One-model episodic adaptation pilot; no superiority claim",
        "model": str(args.model),
        "model_sha256": hashlib.sha256(args.model.read_bytes()).hexdigest(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "seed": args.seed,
        "method_order": order,
        "sequence": SEQUENCE,
        "demands_mbps": [9, 9],
        "scored_trials": 48,
        "warmups": "One action-0 trial in phase A per method; excluded",
        "observation": "Current announced context plus prior-trial utilization",
        "timing": "Decision and whole-step duration; not convergence latency",
        "failure_policy": "Stop on first failure; preserve all evidence",
        "final_state": "Last installed adapter rules remain active",
    }
    (folder / "plan.json").write_text(json.dumps(plan, indent=2))
    print("Evidence:", folder.resolve(), flush=True)
    print("Method order:", order, flush=True)
    rows = []
    method = None
    try:
        for method in order:
            env = SequenceEnv(evaluation=True, horizon=len(SEQUENCE))
            print("Adapter:", env.request("/health"), flush=True)
            obs, reset_info = env.reset(seed=args.seed)
            (folder / f"{method}-warmup.json").write_text(
                json.dumps(reset_info, indent=2)
            )
            for step, (phase, bg_path, bg_mbps) in enumerate(SEQUENCE):
                if (env.demands, env.bg_path, env.bg_mbps) != (
                    [9, 9], bg_path, bg_mbps
                ):
                    raise RuntimeError("Workload sequence mismatch")
                if not env.observation_space.contains(obs):
                    raise RuntimeError("Invalid observation")
                previous_paths = list(env.paths)
                prior_utilization = list(env.utilization)
                probabilities = None
                start = time.perf_counter()
                if method == "ppo":
                    with torch.no_grad():
                        action, _ = model.predict(obs, deterministic=True)
                        tensor, _ = model.policy.obs_to_tensor(obs)
                        probabilities = model.policy.get_distribution(
                            tensor
                        ).distribution.probs.cpu().numpy()[0].tolist()
                    action = int(action)
                else:
                    action = baseline(
                        method, env.demands, bg_path, bg_mbps,
                        env.utilization,
                    )
                decision_ms = (time.perf_counter() - start) * 1000
                # PPO timing includes probability extraction for this diagnostic.
                start = time.perf_counter()
                obs, reward, terminated, truncated, info = env.step(action)
                step_seconds = time.perf_counter() - start
                if terminated or truncated != (step == len(SEQUENCE) - 1):
                    raise RuntimeError("Unexpected episode boundary")
                paths = list(ACTIONS[action])
                rates = info["receiver_mbps"]
                offered = info["offered_mbps"]
                row = {
                    "method": method, "step": step, "phase": phase,
                    "background_path": bg_path, "background_mbps": bg_mbps,
                    "demands_mbps": [9, 9],
                    "action": action, "paths": paths,
                    "previous_paths": previous_paths,
                    "assignment_changes": sum(
                        a != b for a, b in zip(previous_paths, paths)
                    ),
                    "prior_utilization": prior_utilization,
                    "utilization": info["utilization"],
                    "utilization_window_sec": info["utilization_window_sec"],
                    "receiver_mbps": rates,
                    "controlled_sum_mbps": sum(rates[:2]),
                    "controlled_satisfaction": sum(
                        min(r / d, 1) for r, d in zip(rates[:2], offered[:2])
                    ) / 2,
                    "all_flow_satisfaction": sum(
                        min(r / d, 1) for r, d in zip(rates, offered)
                    ) / len(offered),
                    "loss_percent": info["loss_percent"],
                    "reward": reward,
                    "action_probabilities": probabilities,
                    "decision_with_diagnostics_ms": decision_ms,
                    "step_seconds": step_seconds,
                    "evidence": info["evidence"],
                }
                rows.append(row)
                with (folder / "trials.jsonl").open("a") as handle:
                    handle.write(json.dumps(row) + "\n")
                print(json.dumps({
                    k: row[k] for k in (
                        "method", "step", "phase", "action",
                        "controlled_sum_mbps", "assignment_changes"
                    )
                }), flush=True)
            env.close()
        summary = []
        for name in METHODS:
            selected = [r for r in rows if r["method"] == name]
            summary.append({
                "method": name,
                "actions": [r["action"] for r in selected],
                "mean_controlled_mbps": sum(
                    r["controlled_sum_mbps"] for r in selected
                ) / len(selected),
                "mean_controlled_satisfaction": sum(
                    r["controlled_satisfaction"] for r in selected
                ) / len(selected),
                "assignment_changes_including_initial": sum(
                    r["assignment_changes"] for r in selected
                ),
            })
        report = {
            "status": "ADAPTATION_PILOT_EXECUTION_PASS",
            "scored_trials": len(rows),
            "summary": summary,
            "scope": "Functional execution passed; adaptation analysis pending",
        }
        (folder / "summary.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2), flush=True)
    except Exception as exc:
        (folder / "error.json").write_text(json.dumps({
            "method": method, "completed_trials": len(rows),
            "error": str(exc),
        }, indent=2))
        raise


if __name__ == "__main__":
    main()
