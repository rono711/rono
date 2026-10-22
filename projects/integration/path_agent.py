"""Fixed-scope RONO path steering actuator; not an autonomous TE algorithm."""
import argparse
import fcntl
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PREFIX = "10.105.0.0/24"
ROUTER = "clab-bgp7-r2"
PEER = "10.0.23.2"
MAP = "RONO_PATH_R3_IN"
LIST = "RONO_PATH_PREFIX"
EXPECTED = {"direct": ("10.0.25.2", "65005"),
            "alternative": ("10.0.23.2", "65003 65006 65005")}


def vty(*commands):
    cmd = ["docker", "exec", ROUTER, "vtysh"]
    for command in commands:
        cmd.extend(["-c", command])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    output = r.stdout + r.stderr
    if r.returncode or re.search(r"(?mi)^\s*%|unknown command|ambiguous command|processing failure", output):
        raise RuntimeError(output.strip())
    return r.stdout


def routes():
    return json.loads(vty("show bgp ipv4 unicast " + PREFIX + " json"))


def matches(path, target):
    peer, aspath = EXPECTED[target]
    return (path.get("valid") is True and
            path.get("peer", {}).get("peerId") == peer and
            path.get("aspath", {}).get("string", "").strip() == aspath and
            any(n.get("ip") == peer and n.get("accessible") is True
                for n in path.get("nexthops", [])))


def selected(data, target):
    return any(matches(p, target) and p.get("bestpath", {}).get("overall") is True
               for p in data.get("paths", []))


def summarize(data):
    return [{"peer": p.get("peer", {}).get("peerId"),
             "as_path": p.get("aspath", {}).get("string"),
             "local_preference": p.get("locPrf", p.get("localPref", "default")),
             "best": p.get("bestpath", {}).get("overall", False)}
            for p in data.get("paths", [])]


def guard():
    # This first actuator deliberately refuses concurrent DDS bridge control.
    r = subprocess.run(["pgrep", "-af", "[b]gp_bridge.py"],
                       capture_output=True, text=True, timeout=5)
    if r.returncode == 0:
        raise RuntimeError("Stop BGP bridge + restore in the dashboard first")
    if r.returncode != 1:
        raise RuntimeError("Could not check for another router controller")
    config = vty("show running-config")
    if "RONO_DEMO_R5_IN" in config:
        raise RuntimeError("DDS revocation filter remains; restore that experiment first")
    for peer in (PEER, "10.0.25.2"):
        bindings = re.findall(r"(?m)^\s*neighbor " + re.escape(peer) + r" route-map (\S+) in\s*$", config)
        if any(name != MAP or peer != PEER for name in bindings):
            raise RuntimeError("An existing inbound policy would conflict; no changes applied")
    return config


def remove_filter():
    config = vty("show running-config")
    commands = ["configure terminal"]
    if f"neighbor {PEER} route-map {MAP} in" in config:
        commands += ["router bgp 65002", "address-family ipv4 unicast",
                     f"no neighbor {PEER} route-map {MAP} in",
                     "exit-address-family", "exit"]
    if re.search(r"(?m)^route-map " + MAP + r"\s", config):
        commands += [f"no route-map {MAP}"]
    if f"ip prefix-list {LIST} " in config:
        commands += [f"no ip prefix-list {LIST}"]
    vty(*commands, "end")
    vty(f"clear bgp ipv4 unicast {PEER} soft in")


def prefer_alternative():
    vty("configure terminal",
        f"ip prefix-list {LIST} seq 10 permit {PREFIX}",
        f"route-map {MAP} permit 10", f"match ip address prefix-list {LIST}",
        "set local-preference 250", "exit",
        f"route-map {MAP} permit 20", "exit",
        "router bgp 65002", "address-family ipv4 unicast",
        f"neighbor {PEER} route-map {MAP} in", "end")
    vty(f"clear bgp ipv4 unicast {PEER} soft in")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "alternative", "direct", "restore"])
    args = parser.parse_args()
    if args.action == "status":
        print(json.dumps({"prefix": PREFIX, "paths": summarize(routes())}, indent=2))
        return
    root = Path.cwd()
    if not (root / "containerlab/bgp7.clab.yml").is_file():
        raise RuntimeError("Run from ~/research/rono")
    lock_dir = Path.home() / ".cache/rono"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / "router-control.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = guard()
        before = routes()
        target = "direct" if args.action == "restore" else args.action
        if not any(matches(p, target) for p in before.get("paths", [])):
            raise RuntimeError("Requested path is not currently available; no changes applied")
        folder = root / "results/16-path-steering" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        folder.mkdir(parents=True)
        (folder / "before.conf").write_text(config)
        (folder / "before.json").write_text(json.dumps(before, indent=2))
        start = time.monotonic_ns()
        record = {"action": args.action, "target": target, "prefix": PREFIX,
                  "decision_source": "explicit operator preference", "status": "ERROR"}
        try:
            if target == "alternative":
                prefer_alternative()
            else:
                remove_filter()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                after = routes()
                if selected(after, target):
                    record.update(status="PASS", paths=summarize(after),
                                  observed_apply_ms=(time.monotonic_ns() - start) / 1e6)
                    (folder / "after.json").write_text(json.dumps(after, indent=2))
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("Selected BGP path did not match the requested path within 15 seconds")
        except Exception as error:
            record["error"] = str(error)
            try:
                remove_filter()
                record["rollback_requested"] = True
                # Rollback is reported verified only after observing the direct path.
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    rolled = routes()
                    if selected(rolled, "direct"):
                        record["rollback_verified"] = True
                        break
                    time.sleep(0.5)
                else:
                    record["rollback_verified"] = False
            except Exception as rollback_error:
                record["rollback_error"] = str(rollback_error)
            raise
        finally:
            record["results"] = str(folder)
            (folder / "result.json").write_text(json.dumps(record, indent=2))
            print(json.dumps(record, indent=2), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("PATH AGENT ERROR:", error, file=sys.stderr)
        sys.exit(1)
