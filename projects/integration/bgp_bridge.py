"""RONO lab bridge: accepted DDS cache events -> fixed R2 import filter.

Run on the EC2 host from the repository root. No Docker socket in DDS containers.
Only future log entries from the existing domain-72 dashboard subscriber are read.
Stopping that subscriber ends this bridge and restores its temporary filter.
"""
import json
import fcntl
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SUB = "rono-policy-subscriber"
ROUTER = "clab-bgp7-r2"
NEIGHBOR = "10.0.25.2"
PREFIX = "10.105.0.0/24"
MAP = "RONO_DEMO_R5_IN"
LIST = "RONO_DEMO_PREFIX"
running = True
follower = None


def docker(*args):
    result = subprocess.run(["sudo", "-n", "docker", *args],
                            capture_output=True, text=True, timeout=25)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def vty(*commands):
    args = ["exec", ROUTER, "vtysh"]
    for command in commands:
        args += ["-c", command]
    output = docker(*args)
    if re.search(r"(?mi)^\s*%|unknown command|ambiguous command|processing failure", output):
        raise RuntimeError(output)
    return output


def route():
    return vty("show bgp ipv4 unicast " + PREFIX)


def direct_path(text):
    return bool(re.search(r"(?m)^\s*65005\s*\n\s*10\.0\.25\.2 from 10\.0\.25\.2", text))


def check_binding():
    config = vty("show running-config")
    bindings = re.findall(r"(?m)^\s*neighbor 10\.0\.25\.2 route-map (\S+) in\s*$", config)
    if any(name != MAP for name in bindings):
        raise RuntimeError("Another inbound route-map is attached; refusing to replace it")
    return config


def revoke():
    check_binding()
    vty("configure terminal",
        f"ip prefix-list {LIST} seq 10 permit {PREFIX}",
        f"route-map {MAP} deny 10", f"match ip address prefix-list {LIST}",
        "exit", f"route-map {MAP} permit 20", "exit",
        "router bgp 65002", "address-family ipv4 unicast",
        f"neighbor {NEIGHBOR} route-map {MAP} in", "end")
    vty(f"clear bgp ipv4 unicast {NEIGHBOR} soft in")


def restore():
    config = check_binding()
    commands = ["configure terminal"]
    if f"neighbor {NEIGHBOR} route-map {MAP} in" in config:
        commands += ["router bgp 65002", "address-family ipv4 unicast",
                     f"no neighbor {NEIGHBOR} route-map {MAP} in",
                     "exit-address-family", "exit"]
    if re.search(r"(?m)^route-map " + MAP + r"\s", config):
        commands += [f"no route-map {MAP}"]
    if f"ip prefix-list {LIST} " in config:
        commands += [f"no ip prefix-list {LIST}"]
    vty(*commands, "end")
    vty(f"clear bgp ipv4 unicast {NEIGHBOR} soft in")


def wait_route(authorized):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        text = route()
        if direct_path(text) == authorized:
            return text
        time.sleep(0.5)
    raise RuntimeError("BGP verification timed out")


def stop(signum, frame):
    global running
    running = False
    if follower is not None and follower.poll() is None:
        follower.terminate()


def main():
    global follower
    root = Path.cwd()
    if not (root / "projects/heta/fastdds/policy_app.py").is_file():
        raise RuntimeError("Run from ~/research/rono")
    since = datetime.now(timezone.utc).isoformat()
    item = json.loads(docker("inspect", SUB))[0]
    if not item["State"]["Running"]:
        raise RuntimeError("Start the policy subscriber first")
    if (item["Config"].get("Labels") or {}).get("io.rono.dashboard") != "true":
        raise RuntimeError("Unexpected subscriber owner")
    command = item["Config"]["Cmd"]
    if "--domain" not in command or command[command.index("--domain") + 1] != "72":
        raise RuntimeError("This bridge requires subscriber domain 72")
    baseline = check_binding()
    if "RONO_PATH_R3_IN" in baseline or "RONO_PATH_PREFIX" in baseline:
        raise RuntimeError("Restore path-controller baseline before starting the DDS bridge")
    if MAP in baseline or LIST in baseline:
        raise RuntimeError("Demo filter already exists. Restore the manual baseline first")
    before = route()
    if not direct_path(before):
        raise RuntimeError("Baseline direct path is absent; refusing to start")
    folder = root / "results/14-dds-bgp-bridge" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder.mkdir(parents=True)
    (folder / "r2-before.conf").write_text(baseline)
    (folder / "route-before.txt").write_text(before)
    (folder / "subscriber-inspect.json").write_text(json.dumps(item, indent=2))

    def event(kind, **data):
        record = {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
                  "event": kind, **data}
        line = json.dumps(record)
        print(line, flush=True)
        with (folder / "bridge.jsonl").open("a") as stream:
            stream.write(line + "\n")

    latest = -1
    touched = False
    event("bridge_ready", results=str(folder), subscriber=SUB,
          note="Only new cache_updated events; fixed pair 65005/65002")
    follower = subprocess.Popen(
        ["sudo", "-n", "docker", "logs", "--follow", "--since", since, item["Id"]],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    try:
        for line in follower.stdout:
            if not running:
                break
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if not isinstance(data, dict) or data.get("event") != "cache_updated":
                continue
            if (data.get("customer_asn"), data.get("provider_asn")) != (65005, 65002):
                event("ignored_pair")
                continue
            version = data.get("version")
            authorized = data.get("authorized")
            if type(version) is not int or not 1 <= version <= 18446744073709551615 or type(authorized) is not bool:
                event("ignored_invalid_record")
                continue
            if version <= latest:
                event("ignored_old_version", version=version)
                continue
            event("policy_received", version=version, authorized=authorized)
            (folder / f"v{version}-before.txt").write_text(route())
            # Set before mutation so a partial failure also triggers rollback.
            touched = True
            if authorized:
                restore()
            else:
                revoke()
            after = wait_route(authorized)
            (folder / f"v{version}-after.txt").write_text(after)
            latest = version
            event("bgp_applied", version=version, authorized=authorized,
                  direct_path_present=direct_path(after))
        if running:
            raise RuntimeError("Subscriber log stream ended; restart bridge after restarting subscriber")
    finally:
        if follower.poll() is None:
            follower.terminate()
        try:
            follower.wait(timeout=5)
        except subprocess.TimeoutExpired:
            follower.kill()
            follower.wait()
        if touched:
            try:
                restore()
                (folder / "route-after-rollback.txt").write_text(wait_route(True))
                event("rollback_verified", direct_path_present=True)
            except Exception as error:
                event("rollback_error", error=str(error))
                raise
        else:
            event("bridge_stopped", changes_applied=False)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        lock_dir = Path.home() / ".cache/rono"
        lock_dir.mkdir(parents=True, exist_ok=True)
        with (lock_dir / "router-control.lock").open("w") as router_lock:
            fcntl.flock(router_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            main()
    except Exception as error:
        print("BRIDGE ERROR:", error, file=sys.stderr, flush=True)
        sys.exit(1)
