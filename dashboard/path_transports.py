"""REST and DDS adapters converge on one fixed-scope PathIntent agent endpoint."""
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

NAME = "rono-path-intent-receiver"
LABEL = "io.rono.path-transport"
RUN = '''set -e
source /opt/fastdds-python/install/setup.bash
export PYTHONPATH="/opt/fastdds-python/build/fastdds_python_examples/HelloWorldExample/generated_code:${PYTHONPATH:-}"
exec /usr/bin/python3 -u /app/path_dds_transport.py "$@"
'''


def install(base, app_dir):
    root = Path(app_dir).parents[2]
    integration = root / "projects/integration"
    records = {}
    record_lock = threading.Lock()
    receiver_lock = threading.Lock()
    parent = base.Handler

    class Handler(parent):
        def do_POST(self):
            if self.path != "/api/path-intents":
                return super().do_POST()
            if not hmac.compare_digest(self.headers.get("X-RONO-Token", ""), base.TOKEN):
                return self.reply(403, {"error": "Invalid request token"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096:
                    raise ValueError("Invalid request size")
                intent = json.loads(self.rfile.read(size))
                if (not isinstance(intent, dict) or set(intent) != {"schema", "request_id", "action"} or
                        type(intent["schema"]) is not int or intent["schema"] != 1 or
                        intent["action"] not in ("direct", "alternative", "restore") or
                        not isinstance(intent["request_id"], str) or
                        not re.fullmatch(r"[a-f0-9]{32}", intent["request_id"])):
                    raise ValueError("Invalid PathIntent")
                request_id = intent["request_id"]
                with record_lock:
                    entry = records.get(request_id)
                    # Intents are registered by the local controller before transport handoff.
                    if entry is None or entry["intent"] != intent:
                        return self.reply(409, {"error": "Unregistered or conflicting intent"})
                    if entry["status"] == "applying":
                        return self.reply(409, {"error": "Intent already applying"})
                    if entry["status"] == "done":
                        return self.reply(200, entry["result"])
                    if entry["status"] != "pending":
                        return self.reply(409, {"error": "Intent is no longer pending"})
                    entry.update(status="applying", received_monotonic_ns=time.monotonic_ns())
                folder = Path(entry["folder"])
                try:
                    result = subprocess.run(
                        [sys.executable, str(integration / "path_agent.py"), intent["action"]],
                        cwd=root, capture_output=True, text=True)
                    (folder / "agent.stdout.json").write_text(result.stdout)
                    (folder / "agent.stderr.txt").write_text(result.stderr)
                    try:
                        outcome = json.loads(result.stdout)
                    except ValueError:
                        outcome = {"status": "ERROR", "output": result.stdout}
                    if result.returncode:
                        outcome.update(status="ERROR", error=result.stderr.strip() or "Agent failed")
                except Exception as error:
                    outcome = {"status": "ERROR", "error": str(error)}
                with record_lock:
                    entry.update(status="done", result=outcome, completed_monotonic_ns=time.monotonic_ns())
                    (folder / "transport-result.json").write_text(json.dumps(entry, indent=2))
                self.reply(200, outcome)
            except Exception as error:
                self.reply(400, {"error": str(error)})

    base.Handler = Handler

    def ensure_receiver():
        with receiver_lock:
            inspection = base.docker("inspect", NAME, check=False)
            if inspection.returncode == 0:
                item = json.loads(inspection.stdout)[0]
                if (item["Config"].get("Labels") or {}).get(LABEL) != "true":
                    raise RuntimeError("DDS receiver container name belongs to another application")
                same_token = "RONO_AGENT_TOKEN=" + base.TOKEN in (item["Config"].get("Env") or [])
                if item["State"]["Running"] and same_token:
                    return
                base.docker("rm", "-f", NAME)
            elif "no such" not in inspection.stderr.lower():
                raise RuntimeError(inspection.stderr.strip())
            env = dict(os.environ, RONO_AGENT_TOKEN=base.TOKEN)
            result = subprocess.run(["sudo", "-n", "docker", "run", "-d",
                "--name", NAME, "--label", LABEL + "=true",
                "--network", "host", "--ipc", "host", "-e", "RONO_AGENT_TOKEN=" + base.TOKEN,
                "-v", str(integration) + ":/app:ro", "rono/policy-python:0.1",
                "bash", "-c", RUN, "rono", "receiver"],
                capture_output=True, text=True, timeout=30, env=env)
            if result.returncode:
                raise RuntimeError(result.stderr.strip())
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                item = json.loads(base.docker("inspect", NAME).stdout)[0]
                logs = base.docker("logs", NAME)
                if not item["State"]["Running"]:
                    raise RuntimeError("DDS receiver exited: " + logs.stdout + logs.stderr)
                if '"event": "path_receiver_ready"' in logs.stdout:
                    return
                time.sleep(0.25)
            raise RuntimeError("DDS receiver startup timed out")

    def run_intent(action, transport):
        if transport not in ("rest", "dds"):
            raise ValueError("Transport must be REST or DDS")
        if transport == "dds":
            ensure_receiver()
        request_id = uuid.uuid4().hex
        intent = {"schema": 1, "request_id": request_id, "action": action}
        folder = root / "results/18-controller-transports" / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-") + transport)
        folder.mkdir(parents=True)
        (folder / "intent.json").write_text(json.dumps(intent, indent=2))
        with record_lock:
            # Bounded in-memory deduplication; this is not persistent version/recovery state.
            if len(records) >= 512:
                for key in list(records):
                    if records[key]["status"] == "done":
                        del records[key]
                        break
                if len(records) >= 512:
                    raise RuntimeError("Too many outstanding intents")
            records[request_id] = {"intent": intent, "transport": transport,
                                  "folder": str(folder), "status": "pending",
                                  "handoff_monotonic_ns": time.monotonic_ns()}
        try:
            if transport == "rest":
                request = urllib.request.Request("http://127.0.0.1:8502/api/path-intents",
                    data=json.dumps(intent).encode(), method="POST",
                    headers={"Content-Type": "application/json", "X-RONO-Token": base.TOKEN})
                with urllib.request.urlopen(request, timeout=180) as response:
                    json.load(response)
            else:
                result = base.docker("run", "--rm", "--network", "host", "--ipc", "host",
                    "-v", str(integration) + ":/app:ro", "rono/policy-python:0.1",
                    "bash", "-c", RUN, "rono", "sender", json.dumps(intent))
                (folder / "dds-sender.log").write_text(result.stdout + result.stderr)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                with record_lock:
                    entry = records[request_id]
                    if entry["status"] == "done":
                        outcome = dict(entry["result"])
                        outcome.update(transport=transport, request_id=request_id,
                                       transport_results=str(folder),
                                       transport_scope="Functional test; DDS sender startup/discovery included")
                        return SimpleNamespace(returncode=0 if outcome.get("status") == "PASS" else 1,
                                               stdout=json.dumps(outcome), stderr=outcome.get("error", ""))
                time.sleep(0.1)
            raise RuntimeError("Agent result timeout; inspect routing state before retrying")
        except Exception as error:
            with record_lock:
                entry = records[request_id]
                entry["controller_error"] = str(error)
                if entry["status"] == "pending":
                    entry["status"] = "cancelled"
                (folder / "transport-error.json").write_text(json.dumps(entry, indent=2))
            raise

    return run_intent
