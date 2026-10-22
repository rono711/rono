"""Local dashboard test runner. Uses dedicated containers and DDS domain 173."""
import hashlib
import hmac
import json
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def install(base, app_dir):
    app_dir = Path(app_dir)
    root = app_dir.parents[2]
    lock = threading.Lock()
    state = {"running": False, "phase": "Ready", "results": [], "folder": ""}

    def update(**values):
        with lock:
            state.update(values)

    def snapshot():
        with lock:
            return json.loads(json.dumps(state))

    def run_suite():
        folder = root / "results/12-automated-tests" / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
        results = []
        try:
            folder.mkdir(parents=True)
            update(folder=str(folder))
            git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                 capture_output=True, text=True, timeout=10)
            metadata = {"started_utc": datetime.now(timezone.utc).isoformat(),
                        "git_commit": git.stdout.strip(), "domain": 173,
                        "image": base.IMAGE, "negative_observation_seconds": 8,
                        "source_sha256": {str(p.relative_to(root)):
                            hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in (app_dir / "policy_app.py",
                                      root / "dashboard/automated_tests.py",
                                      root / "dashboard/policy_dashboard.py")}}
            metadata["image_inspect"] = json.loads(base.docker("image", "inspect", base.IMAGE).stdout)
            (folder / "environment.json").write_text(json.dumps(metadata, indent=2))
            for case in ("transient-local", "volatile", "history-depth-3"):
                update(phase="Running: " + case)
                case_dir = folder / case
                case_dir.mkdir()
                containers = []
                commands = []
                record = {"test": case, "status": "ERROR"}
                prefix = "rono-test-" + uuid.uuid4().hex[:12]

                def logs(name):
                    r = base.docker("logs", name)
                    return r.stdout + r.stderr

                def alive(name):
                    r = base.docker("inspect", name)
                    return json.loads(r.stdout)[0]["State"]["Running"]

                def events(name, event):
                    found = []
                    for line in logs(name).splitlines():
                        try:
                            item = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(item, dict) and item.get("event") == event:
                            found.append(item)
                    return found

                def wait_for(check, names, description, seconds=15):
                    deadline = time.monotonic() + seconds
                    while time.monotonic() < deadline:
                        if not all(alive(n) for n in names):
                            raise RuntimeError("Container exited during " + description)
                        if check():
                            return
                        time.sleep(0.25)
                    raise AssertionError("Timed out: " + description)

                def start(role, version=1, samples=1, depth=20, durability="transient_local"):
                    name = prefix + "-" + role + "-" + str(len(containers))
                    command = ["run", "-d", "--name", name,
                               "--label", "io.rono.automated-tests=true",
                               "--network", "host", "--ipc", "host",
                               "-v", f"{app_dir}:/app:ro", base.IMAGE,
                               "bash", "-c", base.RUN_SCRIPT, "rono", role,
                               "--domain", "173", "--reliability", "reliable",
                               "--durability", durability, "--history-depth", str(depth)]
                    if role == "publisher":
                        command += ["--customer", "65005", "--provider", "65002",
                                    "--version", str(version), "--action", "authorize",
                                    "--samples", str(samples), "--interval", "0.1",
                                    "--hold-seconds", "120"]
                    else:
                        command += ["--filter-customer", "65005"]
                    commands.append(command)
                    containers.append(name)
                    base.docker(*command)
                    return name

                def published(name, expected):
                    wait_for(lambda: len(events(name, "write_return")) >= len(expected),
                             [name], "publisher completed writes")
                    writes = events(name, "write_return")
                    if [w["version"] for w in writes] != expected or any(
                            w.get("result") != "0" for w in writes):
                        raise AssertionError("Unexpected versions or failed write return")
                    # Record evidence before creating a late subscriber.
                    record["publisher_writes_observed_utc"] = datetime.now(timezone.utc).isoformat()

                try:
                    volatile = case == "volatile"
                    durability = "volatile" if volatile else "transient_local"
                    expected = [107, 108, 109] if case == "history-depth-3" else [11]
                    pub = start("publisher", version=100 if case == "history-depth-3" else 11,
                                samples=10 if case == "history-depth-3" else 1,
                                depth=3 if case == "history-depth-3" else 20,
                                durability=durability)
                    published(pub, list(range(100, 110)) if case == "history-depth-3" else [11])
                    record["subscriber_start_requested_utc"] = datetime.now(timezone.utc).isoformat()
                    sub = start("subscriber", durability=durability)
                    wait_for(lambda: "Policy subscriber ready" in logs(sub),
                             [pub, sub], "subscriber ready")
                    if volatile:
                        until = time.monotonic() + 8
                        while time.monotonic() < until:
                            if not alive(pub) or not alive(sub):
                                raise RuntimeError("Endpoint exited during negative observation")
                            if events(sub, "cache_updated"):
                                raise AssertionError("Volatile subscriber received historical data")
                            time.sleep(0.25)
                        record["late_versions"] = []
                        # Positive control: the same reader must receive a new publication.
                        live_pub = start("publisher", version=12, durability="volatile")
                        published(live_pub, [12])
                        wait_for(lambda: len(events(sub, "cache_updated")) >= 1,
                                 [sub, live_pub], "Volatile live positive control")
                        expected = [12]
                    else:
                        wait_for(lambda: len(events(sub, "cache_updated")) >= len(expected),
                                 [pub, sub], "retained history delivery")
                        time.sleep(1)
                    updates = events(sub, "cache_updated")
                    observed = [item["version"] for item in updates]
                    record.update(expected_versions=expected, observed_versions=observed)
                    if observed != expected or any(
                            item.get("customer_asn") != 65005 or
                            item.get("provider_asn") != 65002 or
                            item.get("authorized") is not True for item in updates):
                        raise AssertionError("Received policy sequence differs from expectation")
                    record["status"] = "PASS"
                except AssertionError as error:
                    record.update(status="FAIL", error=str(error))
                except Exception as error:
                    record.update(status="ERROR", error=str(error))
                finally:
                    cleanup_errors = []
                    for name in containers:
                        try:
                            info = base.docker("inspect", name, check=False)
                            if info.returncode:
                                continue
                            (case_dir / (name + ".inspect.json")).write_text(info.stdout)
                            (case_dir / (name + ".log")).write_text(logs(name))
                        except Exception as error:
                            cleanup_errors.append(str(error))
                        finally:
                            try:
                                removed = base.docker("rm", "-f", name, check=False)
                                if removed.returncode and "no such" not in removed.stderr.lower():
                                    cleanup_errors.append(removed.stderr)
                            except Exception as error:
                                cleanup_errors.append(str(error))
                    if cleanup_errors:
                        record.update(status="ERROR", cleanup_errors=cleanup_errors)
                    (case_dir / "commands.json").write_text(json.dumps(commands, indent=2))
                    (case_dir / "result.json").write_text(json.dumps(record, indent=2))
                results.append(record)
                (folder / "summary.json").write_text(json.dumps(results, indent=2))
                update(results=list(results))
            update(phase="Completed")
        except Exception as error:
            update(phase="ERROR: " + str(error))
        finally:
            update(running=False)

    parent = base.Handler

    class Handler(parent):
        def do_GET(self):
            if self.path == "/api/tests":
                return self.reply(200, snapshot())
            return super().do_GET()

        def do_POST(self):
            if self.path != "/api/tests":
                return super().do_POST()
            if not hmac.compare_digest(self.headers.get("X-RONO-Token", ""), base.TOKEN):
                return self.reply(403, {"error": "Invalid request token"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096:
                    raise ValueError("Invalid request size")
                data = json.loads(self.rfile.read(size))
                if data != {"operation": "run"}:
                    raise ValueError("Invalid action")
                with lock:
                    if state["running"]:
                        return self.reply(409, {"error": "Tests already running"})
                    state.update(running=True, phase="Starting", results=[], folder="")
                threading.Thread(target=run_suite, daemon=False).start()
                return self.reply(202, {"message": "Tests started"})
            except Exception as error:
                return self.reply(400, {"error": str(error)})

    base.Handler = Handler
    panel = '''<section class="card">
<h2>Automated QoS tests</h2>
<p>Fixed test settings: Reliable; separate DDS domain 173.
Tests use dedicated containers. Keep this server running until completion.</p>
<button id="run-tests" onclick="runTests()">Run tests</button>
<pre id="test-results">Ready</pre></section>'''
    script = '''<script>
async function runTests(){
 const button=document.getElementById('run-tests');button.disabled=true;
 try{
  const response=await fetch('/api/tests',{method:'POST',
   headers:{'Content-Type':'application/json','X-RONO-Token':token},
   body:JSON.stringify({operation:'run'})});
  const data=await response.json();if(!response.ok)throw Error(data.error);
 }catch(error){document.getElementById('test-results').textContent=error.message}
 finally{await pollTests()}
}
async function pollTests(){
 try{
  const response=await fetch('/api/tests');
  if(!response.ok)throw Error('Test status unavailable');
  const data=await response.json();
  document.getElementById('run-tests').disabled=data.running;
  document.getElementById('test-results').textContent=data.phase+'\\n'+
   data.results.map(r=>r.status+'  '+r.test+(r.error?' — '+r.error:'')).join('\\n')+
   '\\nResults: '+data.folder;
 }catch(error){document.getElementById('test-results').textContent=error.message}
}
setInterval(pollTests,2000);pollTests();
</script>'''
    base.PAGE = base.PAGE.replace("</main>", panel + "</main>", 1).replace(
        "</html>", script + "</html>", 1)
