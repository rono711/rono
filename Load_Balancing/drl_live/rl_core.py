"""Pure experiment contract; no privileged operations or ML dependencies."""
import math

ACTIONS = ((0, 0), (0, 1), (1, 0), (1, 1))
FLOWS = (("rlh1", "rlh3", "10.54.0.1", 43001, "10.54.0.3", 5401),
         ("rlh2", "rlh4", "10.54.0.2", 43002, "10.54.0.4", 5402),
         ("rlh1", "rlh4", "10.54.0.1", 43003, "10.54.0.4", 5403))


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != {"action", "demands", "background_path", "background_mbps"}:
        raise ValueError("Unexpected request fields")
    a = payload["action"]
    if type(a) is not int or a not in range(4):
        raise ValueError("Action must be integer 0..3")
    demands = payload["demands"]
    if not isinstance(demands, list) or len(demands) != 2:
        raise ValueError("Two demands required")
    for d in demands:
        if type(d) not in (int, float) or not math.isfinite(d) or not 1 <= d <= 30:
            raise ValueError("Demand must be finite and between 1 and 30 Mbps")
    if type(payload["background_path"]) is not int or payload["background_path"] not in (0, 1):
        raise ValueError("Background path must be 0 or 1")
    b = payload["background_mbps"]
    if type(b) not in (int, float) or not math.isfinite(b) or not 0 <= b <= 20:
        raise ValueError("Background load must be finite and between 0 and 20 Mbps")
    return payload


def reward(received, offered, old_paths, new_paths):
    if len(received) != len(offered) or not offered:
        raise ValueError("Missing flow metrics")
    if any(not math.isfinite(r) or r < 0 for r in received):
        raise ValueError("Invalid receiver rate")
    satisfaction = sum(min(r / d, 1.0) for r, d in zip(received, offered)) / len(offered)
    changes = sum(a != b for a, b in zip(old_paths, new_paths)) / 2
    return satisfaction - 0.02 * changes


def observation(demands, background_path, background_mbps, utilization, paths):
    if len(utilization) != 8 or any(not math.isfinite(u) or u < 0 for u in utilization):
        raise ValueError("Invalid utilization vector")
    # Utilization is clipped only in the neural-network observation; raw values
    # and the clipping count remain in per-step evidence.
    return ([d / 30 for d in demands] +
            [background_mbps / 20 if background_path == p else 0 for p in (0, 1)] +
            [min(u, 2.0) / 2 for u in utilization] + list(paths))
