"""Loopback-only authenticated Mininet adapter. Start with start(net) in CLI.

Fixed operations and flow tuples only; never executes supplied shell commands.
No simulation: every step enforces UDP paths and runs live iperf3 receivers.
"""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from datetime import datetime, timezone
from rono_lb_baseline import api
from rono_lb_actions import rules_for, wait_rules
from rl_core import ACTIONS, FLOWS, validate

PRIORITY = 23000
PORT = 8766
TOKEN = Path(__file__).with_name(".rl-token")
TRANSIT = [(f"of:{0x5400+s:016x}", p) for s, ports in
           [(1, (3,4)), (2,(1,2)), (3,(1,2)), (4,(1,2))] for p in ports]


def owned(path):
    if os.geteuid() == 0 and os.environ.get("SUDO_UID"):
        os.chown(path, int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"]))


def write(path, data):
    path.write_text(json.dumps(data, indent=2))
    owned(path)


def snapshot():
    raw = api("/statistics/ports")
    ports = {(d["device"], int(p["port"])): p for d in raw.get("statistics", [])
             for p in d.get("ports", []) if str(p["port"]).isdigit()}
    if not all(k in ports for k in TRANSIT):
        raise RuntimeError("Missing transit counters")
    return raw, ports


def udp_rules(flow, path):
    _, _, src, sport, dst, dport = flow
    rules = rules_for((src, sport, dst, dport), path)
    for rule in rules:
        rule["priority"] = PRIORITY
        for criterion in rule["selector"]["criteria"]:
            if criterion["type"] == "IP_PROTO":
                criterion["protocol"] = 17
            elif criterion["type"] in ("TCP_SRC", "TCP_DST"):
                criterion["type"] = criterion["type"].replace("TCP", "UDP")
                criterion["udpPort"] = criterion.pop("tcpPort")
    return rules


def measure(net, payload, folder):
    validate(payload)
    paths = ACTIONS[payload["action"]]
    offered = list(payload["demands"])
    selected = list(paths)
    flows = list(FLOWS[:2])
    if payload["background_mbps"] > 0:
        flows.append(FLOWS[2])
        offered.append(payload["background_mbps"])
        selected.append(payload["background_path"])
    # Always configure background tuple too, so its previous path never leaks
    # into the next workload. No old baseline rules are removed.
    rules = [r for f,p in zip(FLOWS, list(paths)+[payload["background_path"]])
             for r in udp_rules(f,p)]
    for downstream in (True, False):
        group = [r for i,r in enumerate(rules) if (i%6 < 4) == downstream]
        api("/flows?appId=org.onosproject.cli", {"flows":group})
        wait_rules(group)
    wait_rules(rules)
    write(folder/"request.json", payload)
    write(folder/"rules.json", rules)
    clients, servers, handles = [], [], []
    try:
        for f in flows:
            src_h, dst_h, src, sport, dst, dport = f
            out = open(folder/f"server-{dport}.json", "w")
            handles.append(out)
            servers.append(net.get(dst_h).popen(
                ["iperf3", "-s", "-1", "-p", str(dport), "-J"], stdout=out, stderr=subprocess.STDOUT))
        time.sleep(0.7)
        if any(p.poll() is not None for p in servers):
            raise RuntimeError("Server exited before test; check occupied ports 5401..5403")
        raw_a, a = snapshot()
        write(folder/"counters-before.json", raw_a)
        launch_times = []
        for f, demand in zip(flows, offered):
            src_h, dst_h, src, sport, dst, dport = f
            out = open(folder/f"client-{dport}.json", "w")
            handles.append(out)
            launch_times.append(time.monotonic())
            clients.append(net.get(src_h).popen(
                ["iperf3", "-c", dst, "-p", str(dport), "--cport", str(sport),
                 "-u", "-b", f"{demand}M", "-l", "1200", "-t", "10", "-O", "2", "-J"],
                stdout=out, stderr=subprocess.STDOUT))
        codes = [p.wait(timeout=25) for p in clients]
        if any(c != 0 for c in codes):
            raise RuntimeError(f"iperf clients failed {codes}; raw logs retained")
        for p in servers:
            if p.wait(timeout=8) != 0:
                raise RuntimeError("iperf server failure")
        for h in handles:
            h.flush()
        # Allow a provider polling cycle. Raw durations define utilization's
        # interval; this includes settling time and is not the iperf window.
        time.sleep(6)
        raw_b, b = snapshot()
        write(folder/"counters-after.json", raw_b)
        rates, losses = [], []
        for f in flows:
            data = json.loads((folder/f"client-{f[5]}.json").read_text())
            if data.get("error"):
                raise RuntimeError(data["error"])
            received = data["end"]["sum_received"]
            rates.append(float(received["bits_per_second"])/1e6)
            if "lost_percent" not in received:
                raise RuntimeError("Receiver loss metric missing; raw JSON retained")
            losses.append(float(received["lost_percent"]))
        utilization = []
        intervals = []
        for k in TRANSIT:
            dt = b[k]["durationSec"]-a[k]["durationSec"]
            db = b[k]["bytesSent"]-a[k]["bytesSent"]
            if dt <= 0 or db < 0:
                raise RuntimeError("Stale/reset counters: sample rejected")
            utilization.append(db*8/dt/20e6)
            intervals.append(dt)
        result = {"status":"MEASUREMENT_PASS", "paths":paths, "offered_mbps":offered,
                  "receiver_mbps":rates, "loss_percent":losses, "utilization":utilization,
                  "utilization_window_sec":intervals,
                  "launch_skew_ms":1000*(max(launch_times)-min(launch_times)),
                  "utilization_clip_count":sum(u>2 for u in utilization),
                  "traffic":"UDP; two controlled flows plus optional background",
                  "scope":"Live episodic path allocation, not continuous TCP rerouting",
                  "evidence":str(folder.resolve())}
        write(folder/"summary.json", result)
        return result
    finally:
        for p in clients+servers:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=3)
        for h in handles:
            h.close()
        for p in folder.iterdir():
            if p.is_file():
                owned(p)


def start(net):
    token = TOKEN.read_text().strip()
    if len(token)<32:
        raise RuntimeError("Missing local adapter token")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def respond(self, status, value):
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def authorized(self):
            return hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer "+token)
        def do_GET(self):
            if not self.authorized():
                self.respond(401, {"error":"unauthorized"})
            elif self.path == "/health":
                self.respond(200, {"status":"READY", "mode":"live_Mininet", "port":PORT})
            else:
                self.respond(404, {"error":"not found"})
        def do_POST(self):
            if not self.authorized():
                self.respond(401, {"error":"unauthorized"}); return
            if self.path != "/step":
                self.respond(404, {"error":"not found"}); return
            self.connection.settimeout(5)
            folder = None
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0<size<4096:
                    raise ValueError("Invalid body size")
                payload = validate(json.loads(self.rfile.read(size)))
                folder = Path("results/21-cn-load-balancing/drl-live") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                folder.mkdir(parents=True)
                owned(folder.parent); owned(folder)
                result = measure(net,payload,folder)
            except Exception as exc:
                result = {"error":str(exc), "status":"ERROR", "evidence":str(folder) if folder else None}
                if folder:
                    write(folder/"error.json", result)
                self.respond(500,result); return
            self.respond(200,result)
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    server.timeout=1
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    print(f"Live Mininet adapter READY on 127.0.0.1:{PORT}; token is not printed.")
    print("Keep this Mininet session open; use only one experiment client.")
    return server
