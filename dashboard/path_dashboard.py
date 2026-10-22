"""Dashboard controls for operator steering and one-shot configured-cost selection."""
import hmac
from path_transports import install as install_path_transports
import importlib.util
import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path


def install(base, app_dir):
    root = Path(app_dir).parents[2]
    script = root / "projects/integration/path_agent.py"
    lock = threading.Lock()
    state = {"running": False, "result": None, "error": "", "folder": ""}
    base._rono_path_busy = lambda: state["running"]

    def agent_module():
        spec = importlib.util.spec_from_file_location("rono_path_agent", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    run_intent = install_path_transports(base, app_dir)
    parent = base.Handler

    class Handler(parent):
        def do_GET(self):
            if self.path != "/api/path-control":
                return super().do_GET()
            try:
                with lock:
                    payload = dict(state)
                agent = agent_module()
                data = agent.routes()
                payload["paths"] = agent.summarize(data)
                payload["prefix"] = agent.PREFIX
                self.reply(200, payload)
            except Exception as error:
                self.reply(500, {"error": str(error)})

        def do_POST(self):
            if self.path != "/api/path-control":
                return super().do_POST()
            if not hmac.compare_digest(self.headers.get("X-RONO-Token", ""), base.TOKEN):
                return self.reply(403, {"error": "Invalid request token"})
            acquired = False
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096:
                    raise ValueError("Invalid request size")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("Invalid request")
                transport_name = data.get("transport", "rest")
                if transport_name not in ("rest", "dds"):
                    raise ValueError("Choose REST or DDS")
                operation = data.get("operation")
                if operation not in ("direct", "alternative", "restore", "lowest_cost"):
                    raise ValueError("Invalid path action")
                acquired = lock.acquire(blocking=False)
                if not acquired:
                    return self.reply(409, {"error": "Path operation already running"})
                if state["running"]:
                    return self.reply(409, {"error": "Path operation already running"})
                state.update(running=True, error="", result=None)
                agent = agent_module()
                agent.guard()
                decision = {"transport": transport_name, "requested_operation": operation,
                            "decision_source": "explicit operator preference"}
                action = operation
                if operation == "lowest_cost":
                    costs = {}
                    for key in ("direct", "alternative"):
                        raw = str(data.get(key + "_cost", "")).strip()
                        if not raw.isdigit() or not 1 <= int(raw) <= 1000000:
                            raise ValueError("Configured costs must be integers from 1 to 1000000")
                        costs[key] = int(raw)
                    routes = agent.routes()
                    candidates = [name for name in costs if any(
                        agent.matches(path, name) for path in routes.get("paths", []))]
                    if not candidates:
                        raise ValueError("Neither expected valid path is available")
                    action = min(candidates, key=lambda name: (costs[name], name != "direct"))
                    decision.update(decision_source="configured cost; lower wins; ties prefer direct",
                                    configured_costs=costs, available_candidates=candidates,
                                    selected=action,
                                    note="Configured scores, not measured latency or congestion")
                folder = root / "results/17-path-controller" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                folder.mkdir(parents=True)
                state["folder"] = str(folder)
                (folder / "decision.json").write_text(json.dumps(decision, indent=2))
                # A worker keeps polling responsive while the agent verifies convergence.
                state["result"] = {"decision": decision, "status": "RUNNING"}

                def apply():
                    try:
                        # No timeout kill: the agent owns verification and rollback deadlines.
                        result = run_intent(action, transport_name)
                        (folder / "agent.stdout.json").write_text(result.stdout)
                        (folder / "agent.stderr.txt").write_text(result.stderr)
                        try:
                            record = json.loads(result.stdout)
                        except ValueError:
                            record = {"status": "ERROR", "output": result.stdout}
                        if result.returncode:
                            record["status"] = "ERROR"
                            record["error"] = result.stderr.strip() or "Agent failed"
                        outcome = {"decision": decision, "agent": record,
                                   "status": record.get("status", "ERROR")}
                        (folder / "result.json").write_text(json.dumps(outcome, indent=2))
                        with lock:
                            state.update(result=outcome, error=record.get("error", ""))
                    except Exception as error:
                        with lock:
                            state.update(error=str(error), result={"status": "ERROR"})
                    finally:
                        with lock:
                            state["running"] = False

                threading.Thread(target=apply, daemon=False).start()
                self.reply(202, {"message": "Applying " + action + "; wait for PASS"})
            except Exception as error:
                if acquired:
                    state.update(running=False, error=str(error))
                self.reply(400, {"error": str(error)})
            finally:
                if acquired:
                    lock.release()

    base.Handler = Handler
    panel = '''<section class="card">
<h2>BGP path controller</h2>
<p>R2 → 10.105.0.0/24. Stop the DDS BGP bridge before steering.
Costs are operator-configured scores, not measured network delay.</p>
<div class="controls">
<label>Policy transport<select id="path-transport" style="padding:10px;font:inherit">
<option value="rest">REST / HTTP</option><option value="dds">DDS Reliable</option>
</select></label>
<label>Direct path cost<input id="direct-cost" type="number" min="1" max="1000000" value="10"></label>
<label>Alternative path cost<input id="alternative-cost" type="number" min="1" max="1000000" value="30"></label>
</div>
<button class="path-control" onclick="pathAction('lowest_cost')">Choose lowest cost</button>
<button class="path-control" onclick="pathAction('direct')">Prefer Direct</button>
<button class="path-control" onclick="pathAction('alternative')">Prefer Alternative</button>
<button class="path-control stop" onclick="pathAction('restore')">Restore baseline</button>
<p id="path-message" role="status"></p>
<div class="grid"><div><h3>Available BGP paths</h3><pre id="path-state"></pre></div>
<div><h3>Decision and verification</h3><pre id="path-result">Ready</pre></div></div>
<p>Selection runs once per click. It chooses a valid expected path with the lower
configured cost; ties prefer Direct. Restore baseline before starting the DDS bridge.
REST and DDS send the same PathIntent to one agent. DDS uses separate domain 174.
These are functional checks; sender startup/discovery is not controlled for benchmarking.
This is a fixed-scope research controller, not autonomous congestion-based TE.</p>
</section>'''
    js = '''<script>
let pathBusy=false,pathPolling=false;
async function refreshPaths(){
 if(pathPolling)return;pathPolling=true;
 try{
  const r=await fetch('/api/path-control');const d=await r.json();
  if(!r.ok)throw Error(d.error);
  document.getElementById('path-state').textContent=JSON.stringify(d.paths,null,2);
  document.getElementById('path-result').textContent=JSON.stringify(d.result,null,2)+
   '\\nResults: '+d.folder+(d.error?'\\n'+d.error:'');
  document.querySelectorAll('.path-control').forEach(b=>b.disabled=d.running||pathBusy);
 }catch(e){document.getElementById('path-message').textContent=e.message}
 finally{pathPolling=false}
}
async function pathAction(operation){
 if(pathBusy)return;pathBusy=true;
 document.querySelectorAll('.path-control').forEach(b=>b.disabled=true);
 try{
  const r=await fetch('/api/path-control',{method:'POST',
   headers:{'Content-Type':'application/json','X-RONO-Token':token},
   body:JSON.stringify({operation,transport:document.getElementById("path-transport").value,
    direct_cost:document.getElementById('direct-cost').value,
    alternative_cost:document.getElementById('alternative-cost').value})});
  const d=await r.json();if(!r.ok)throw Error(d.error);
  document.getElementById('path-message').textContent=d.message;
 }catch(e){document.getElementById('path-message').textContent=e.message}
 finally{pathBusy=false;await refreshPaths()}
}
setInterval(refreshPaths,3000);refreshPaths();
</script>'''
    base.PAGE = base.PAGE.replace("</main>", panel + "</main>", 1).replace("</html>", js + "</html>", 1)
