"""GUI controls for the fixed-scope RONO bridge; local HTTP token required."""
import hmac
import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path


def install(base, app_dir):
    root = Path(app_dir).parents[2]
    script = root / "projects/integration/bgp_bridge.py"
    lock = threading.Lock()
    process = None
    log_path = None

    def status():
        with lock:
            current = process
            path = log_path
            code = current.poll() if current else None
        logs = ""
        if path and path.exists():
            with path.open("rb") as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 24000))
                logs = stream.read().decode("utf-8", errors="replace")
        route = base.docker("exec", "clab-bgp7-r2", "vtysh", "-c",
                            "show bgp ipv4 unicast 10.105.0.0/24", check=False)
        return {"state": "running" if current and code is None else
                ("stopped" if current else "not started"),
                "exit_code": code, "logs": logs,
                "route": route.stdout + route.stderr,
                "route_ok": route.returncode == 0,
                "log_file": str(path) if path else ""}

    parent = base.Handler

    class Handler(parent):
        def do_GET(self):
            if self.path != "/api/bgp":
                return super().do_GET()
            try:
                self.reply(200, status())
            except Exception as error:
                self.reply(500, {"error": str(error)})

        def do_POST(self):
            nonlocal process, log_path
            if self.path != "/api/bgp":
                return super().do_POST()
            if not hmac.compare_digest(self.headers.get("X-RONO-Token", ""), base.TOKEN):
                return self.reply(403, {"error": "Invalid request token"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096:
                    raise ValueError("Invalid request size")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict) or data.get("operation") not in ("start", "stop"):
                    raise ValueError("Invalid bridge action")
                with lock:
                    if data["operation"] == "start":
                        if process and process.poll() is None:
                            raise ValueError("Bridge already running")
                        if not script.is_file():
                            raise ValueError("Missing projects/integration/bgp_bridge.py")
                        # Prevent two bridges from competing over one router.
                        check = subprocess.run(["pgrep", "-af", "[b]gp_bridge.py"],
                                               capture_output=True, text=True, timeout=5)
                        if check.returncode == 0:
                            raise ValueError("A bridge is already running in another terminal. Stop it with Ctrl+C first.")
                        if check.returncode != 1:
                            raise RuntimeError("Could not check for an existing bridge")
                        folder = root / "results/15-bgp-dashboard"
                        folder.mkdir(parents=True, exist_ok=True)
                        log_path = folder / (datetime.now(timezone.utc).strftime(
                            "%Y%m%dT%H%M%S%fZ") + ".log")
                        with log_path.open("w") as stream:
                            process = subprocess.Popen([sys.executable, "-u", str(script)],
                                                       cwd=root, stdout=stream,
                                                       stderr=subprocess.STDOUT)
                        message = "Bridge starting; wait for bridge_ready before publishing"
                    else:
                        if process and process.poll() is None:
                            process.terminate()
                            try:
                                process.wait(timeout=45)
                            except subprocess.TimeoutExpired:
                                raise RuntimeError("Rollback still running; check logs before any further changes")
                        if process and process.returncode != 0:
                            raise RuntimeError("Bridge ended with an error; inspect bridge logs")
                        message = "Bridge stopped; confirm rollback_verified in logs if policies were applied"
                self.reply(200, {"message": message})
            except Exception as error:
                self.reply(400, {"error": str(error)})

    base.Handler = Handler
    panel = '''<section class="card">
<h2>DDS → BGP integration</h2>
<p>Fixed scope: customer 65005, provider 65002, prefix 10.105.0.0/24 on R2.
Start the domain-72 subscriber first. Keep it running while the bridge runs.</p>
<button class="bgp-control" onclick="bgpAction('start')">Start BGP bridge</button>
<button class="bgp-control stop" onclick="bgpAction('stop')">Stop bridge + restore</button>
<p id="bgp-message" role="status"></p>
<div class="badge" id="bgp-state">Loading…</div>
<div class="grid"><div><h3>Bridge events</h3><pre id="bgp-log"></pre></div>
<div><h3>R2 current route</h3><pre id="bgp-route"></pre></div></div>
<p>Wait for bridge_ready before publishing. Check bgp_applied for route verification.
Stop the bridge before closing the dashboard; stopping restores the temporary filter.</p>
</section>'''
    script_js = '''<script>
let bgpBusy=false,bgpPolling=false;
async function refreshBgp(){
 if(bgpPolling)return;bgpPolling=true;
 try{
  const r=await fetch('/api/bgp');const d=await r.json();
  if(!r.ok)throw Error(d.error);
  document.getElementById('bgp-state').textContent=d.state+
   (d.exit_code===null?'':' · exit '+d.exit_code);
  document.getElementById('bgp-log').textContent=d.logs;
  document.getElementById('bgp-route').textContent=d.route;
 }catch(e){document.getElementById('bgp-message').textContent=e.message}
 finally{bgpPolling=false}
}
async function bgpAction(operation){
 if(bgpBusy)return;bgpBusy=true;
 document.querySelectorAll('.bgp-control').forEach(b=>b.disabled=true);
 document.getElementById('bgp-message').textContent=operation==='stop'?'Stopping and restoring…':'Starting…';
 try{
  const r=await fetch('/api/bgp',{method:'POST',
   headers:{'Content-Type':'application/json','X-RONO-Token':token},
   body:JSON.stringify({operation})});const d=await r.json();
  if(!r.ok)throw Error(d.error);
  document.getElementById('bgp-message').textContent=d.message;
 }catch(e){document.getElementById('bgp-message').textContent=e.message}
 finally{bgpBusy=false;document.querySelectorAll('.bgp-control').forEach(b=>b.disabled=false);await refreshBgp()}
}
setInterval(refreshBgp,3000);refreshBgp();
</script>'''
    base.PAGE = base.PAGE.replace("</main>", panel + "</main>", 1).replace(
        "</html>", script_js + "</html>", 1)
