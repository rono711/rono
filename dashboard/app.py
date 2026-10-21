import hmac
import json
import re
import secrets
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 8501
TOKEN = secrets.token_urlsafe(32)
IMAGE = "rono/fastdds-python:2.6.2"
OWNER = "io.rono.dashboard"
NAMES = {
    "subscriber": "rono-dashboard-subscriber",
    "publisher": "rono-dashboard-publisher",
}

# User inputs are passed as separate arguments, never inserted into shell code.
RUN_SCRIPT = r"""
set -e
source /opt/fastdds-python/install/setup.bash
cd /opt/fastdds-python/src/fastdds_python/fastdds_python_examples/HelloWorldExample
export PYTHONPATH="/opt/fastdds-python/build/fastdds_python_examples/HelloWorldExample/generated_code:${PYTHONPATH:-}"
exec /usr/bin/python3 -u HelloWorldExample.py "$@"
"""


def docker(*args, check=True):
    result = subprocess.run(
        ["sudo", "-n", "docker", *args],
        capture_output=True, text=True, timeout=30,
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result


def inspect(role):
    result = docker("inspect", NAMES[role], check=False)
    if result.returncode:
        if "no such" in result.stderr.lower():
            return None
        raise RuntimeError(result.stderr.strip())
    item = json.loads(result.stdout)[0]
    labels = item["Config"].get("Labels") or {}
    if labels.get(OWNER) != "true":
        raise RuntimeError("Container name belongs to another application.")
    return item


def status(role):
    item = inspect(role)
    if item is None:
        return {"state": "not started", "count": 0, "logs": ""}
    result = docker("logs", "--tail", "200", NAMES[role])
    logs = result.stdout + result.stderr
    prefix = "Sending" if role == "publisher" else "Received"
    count = len(re.findall(
        rf"^{prefix} Hello World.* : \d+\s*$", logs, re.MULTILINE
    ))
    return {
        "state": item["State"]["Status"],
        "count": count,
        "logs": logs,
    }


def start(role, data):
    domain = int(data.get("domain", 71))
    if not 0 <= domain <= 200:
        raise ValueError("Domain ID must be between 0 and 200.")
    label = str(data.get("label", "RONO")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", label):
        raise ValueError("Label: use 1–32 letters, numbers, underscores or hyphens.")

    item = inspect(role)
    if item is not None:
        if item["State"]["Running"]:
            raise ValueError(f"{role} is already running. Stop it first.")
        docker("rm", NAMES[role])

    command = [
        "run", "-d", "-i",
        "--name", NAMES[role],
        "--label", f"{OWNER}=true",
        "--network", "host", "--ipc", "host",
        IMAGE, "bash", "-c", RUN_SCRIPT, "rono",
        "-p", role, "-d", str(domain),
    ]
    if role == "publisher":
        command += ["-m", label]
    docker(*command)


PAGE = r"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RONO DDS Dashboard</title>
<style>
body{margin:0;background:#101827;color:#e7edf7;font:16px system-ui}
main{max-width:1100px;margin:auto;padding:32px}
h1{margin-bottom:4px}p{color:#aebed2;line-height:1.5}
.controls,.card{background:#19263a;padding:22px;border:1px solid #33445e;
border-radius:12px;margin-top:20px}
.controls{display:flex;gap:20px;align-items:end;flex-wrap:wrap}
label{display:grid;gap:8px}
input{padding:10px;border-radius:6px;border:1px solid #65768d;
background:#101827;color:white;font:inherit;width:170px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px}
button{padding:10px 16px;margin:6px 8px 6px 0;border:0;border-radius:6px;
font:inherit;cursor:pointer;background:#5ae0b5;color:#10241f}
button.stop{background:#f5b7b7}button:disabled{opacity:.5;cursor:wait}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0b1220;
padding:14px;height:280px;overflow:auto;font-size:13px}
#message{min-height:24px;color:#ffd486}
.badge{color:#5ae0b5}small{color:#aebed2}
</style>
<main>
<h1>RONO</h1>
<p>Fast DDS Python control panel · HelloWorld baseline</p>
<div class="controls">
<label>Domain ID<input id="domain" type="number" min="0" max="200" value="71"></label>
<label>Publisher label<input id="label" value="RONO" maxlength="32"></label>
<small>10 messages per publisher run.<br>Use the same domain for both endpoints.</small>
</div>
<p id="message" role="status"></p>
<div class="grid">
<div class="card">
<h2>Subscriber</h2>
<div class="badge" id="subscriber-status">Loading…</div>
<button onclick="action('subscriber','start')">Start subscriber</button>
<button class="stop" onclick="action('subscriber','stop')">Stop</button>
<pre id="subscriber-log"></pre>
</div>
<div class="card">
<h2>Publisher</h2>
<div class="badge" id="publisher-status">Loading…</div>
<button onclick="action('publisher','start')">Start publisher</button>
<button class="stop" onclick="action('publisher','stop')">Stop</button>
<pre id="publisher-log"></pre>
</div>
</div>
<p>Start the subscriber first, then the publisher.
Publisher waits for discovery and exits after sending.
Subscriber remains running until stopped.</p>
<p>Messages use the bundled example's default QoS.
Counters describe this run; they do not establish a reliability guarantee.</p>
</main>
<script>
const token="__TOKEN__";
let busy=false;
async function refresh(){
  const response=await fetch("/api/status");
  const data=await response.json();
  if(!response.ok)throw Error(data.error);
  for(const role of ["subscriber","publisher"]){
    document.getElementById(role+"-status").textContent=
      data[role].state+" · "+data[role].count+" messages";
    const log=document.getElementById(role+"-log");
    if(log.textContent!==data[role].logs){
      log.textContent=data[role].logs;log.scrollTop=log.scrollHeight;
    }
  }
}
async function action(role,operation){
  if(busy)return;
  busy=true;
  document.querySelectorAll("button").forEach(b=>b.disabled=true);
  try{
    const response=await fetch("/api/action",{
      method:"POST",
      headers:{"Content-Type":"application/json","X-RONO-Token":token},
      body:JSON.stringify({
        role,operation,
        domain:document.getElementById("domain").value,
        label:document.getElementById("label").value
      })
    });
    const data=await response.json();
    if(!response.ok)throw Error(data.error);
    document.getElementById("message").textContent=data.message;
    await refresh();
  }catch(error){document.getElementById("message").textContent=error.message}
  finally{
    busy=false;
    document.querySelectorAll("button").forEach(b=>b.disabled=false);
  }
}
async function poll(){
  try{if(!busy)await refresh()}
  catch(error){document.getElementById("message").textContent=error.message}
  setTimeout(poll,2000);
}
poll();
</script></html>"""


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, content, kind="application/json"):
        payload = (json.dumps(content) if kind == "application/json"
                   else content).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        try:
            if self.path == "/":
                self.reply(200, PAGE.replace("__TOKEN__", TOKEN), "text/html")
            elif self.path == "/api/status":
                self.reply(200, {role: status(role) for role in NAMES})
            else:
                self.reply(404, {"error": "Not found"})
        except Exception as error:
            self.reply(500, {"error": str(error)})

    def do_POST(self):
        if self.path != "/api/action":
            return self.reply(404, {"error": "Not found"})
        if not hmac.compare_digest(self.headers.get("X-RONO-Token", ""), TOKEN):
            return self.reply(403, {"error": "Invalid request token"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(size))
            role = data.get("role")
            operation = data.get("operation")
            if role not in NAMES or operation not in ("start", "stop"):
                raise ValueError("Invalid action")
            if operation == "start":
                start(role, data)
            else:
                item = inspect(role)
                if item is not None and item["State"]["Running"]:
                    docker("stop", "--time", "3", NAMES[role])
            self.reply(200, {"message": f"{role}: {operation} completed"})
        except Exception as error:
            self.reply(400, {"error": str(error)})


if __name__ == "__main__":
    print(f"RONO dashboard: http://{HOST}:{PORT}", flush=True)
    print("Press Ctrl+C to stop the dashboard.", flush=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped. DDS containers keep their current state.")
    finally:
        server.server_close()