#!/usr/bin/env python3
"""RONO Security Command Center - read-only dashboard."""

import csv
import json
import subprocess
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORT = 8504

CAMPAIGN = (
    ROOT / "BGP_Security/results/22-security-asap/"
    "20261026T071157618832Z-fig9-campaign/simulation/data.csv"
)

HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RONO | BGP Security Command Center</title>
<style>
:root{
 --bg:#0e1916;--panel:#1b3029;--green:#50d998;
 --red:#ee6870;--yellow:#f1c65d;--text:#f2f8f4;
 --muted:#b5cfc1;
}
*{box-sizing:border-box}
body{
 margin:0;background:var(--bg);color:var(--text);
 font-family:Inter,Segoe UI,Arial,sans-serif;
}
main{max-width:1120px;margin:auto;padding:22px}
header{
 display:flex;justify-content:space-between;
 align-items:center;gap:12px;margin-bottom:18px;
}
.logo{font-size:43px;font-weight:900;letter-spacing:-2px}
.logo .ro{color:var(--red)}
.logo .no{color:var(--green)}
.tagline{font-size:12px;color:var(--muted)}
.panel{
 background:var(--panel);border:1px solid #385849;
 border-radius:15px;padding:18px;margin-bottom:15px;
}
h2{font-size:17px;margin:0 0 12px}
.muted{color:var(--muted);font-size:13px;line-height:1.6}
.status{
 border:1px solid #41614e;border-radius:30px;
 padding:9px 13px;font-size:12px;
}
.dot{
 display:inline-block;width:11px;height:11px;
 border-radius:50%;background:var(--green);
 margin-right:6px;
}
.grid{display:grid;grid-template-columns:1.3fr 1fr;gap:15px}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
button{
 border:1px solid #50715f;background:#254638;
 color:white;border-radius:9px;padding:10px 13px;
 cursor:pointer;font-weight:600;
}
button:hover,button.active{
 background:#357354;border-color:var(--green);
}
svg{width:100%;height:auto}
.route{
 fill:none;stroke-width:6;stroke-linecap:round;
 transition:stroke .4s,opacity .4s;
}
.legit{stroke:var(--green)}
.leak{stroke:var(--red);stroke-dasharray:12 8}
.flow{
 stroke-dasharray:12 8;
 animation:move 1s linear infinite;
}
@keyframes move{to{stroke-dashoffset:-40}}
.node{fill:#29483c;stroke:#9bc4ac;stroke-width:1.5}
.node-label{fill:white;font-size:11px;text-anchor:middle}
.metric{
 display:flex;justify-content:space-between;
 padding:9px 0;border-bottom:1px solid #365143;
 font-size:13px;
}
.bar{
 height:15px;background:#30483e;border-radius:9px;
 overflow:hidden;flex:1;
}
.fill{
 height:100%;background:var(--green);
 transition:width .5s;
}
.barrow{
 display:grid;grid-template-columns:125px 1fr 90px;
 gap:10px;align-items:center;margin:12px 0;font-size:12px;
}
.decision{
 background:#203b30;border-left:4px solid var(--green);
 border-radius:8px;padding:13px;line-height:1.6;
 margin-top:12px;font-size:14px;
}
footer{font-size:11px;color:var(--muted);padding:12px}
@media(max-width:760px){
 .grid{grid-template-columns:1fr}
 main{padding:12px}
 .barrow{grid-template-columns:95px 1fr 75px}
}
@media(prefers-reduced-motion:reduce){
 .flow{animation:none}
}

.security-values{
 display:grid;
 grid-template-columns:repeat(3,minmax(0,1fr));
 gap:10px;
 margin:14px 0;
}
.security-values>div{
 background:#132920;
 border:1px solid #41614e;
 border-radius:9px;
 padding:12px;
}
.security-values span{
 display:block;
 color:#b5cfc1;
 font-size:12px;
}
.security-values strong{
 display:block;
 margin-top:8px;
 overflow-wrap:anywhere;
}
#security-demo h3{
 font-size:15px;
 margin:16px 0 10px;
}
#security-stage-buttons button.active{
 background:#357354;
 border-color:#50d998;
}
@media(max-width:700px){
 .security-values{grid-template-columns:1fr}
}
</style>
</head>
<body>
<main>
<header>
 <div>
  <div class="logo"><span class="ro">RO</span><span class="no">NO</span></div>
  <div class="tagline">Routing Orchestration and Network Optimization</div>
  <div class="tagline">Intelligent Routing · Balanced Traffic · Reliable Networks</div>
 </div>
 <div class="status"><span class="dot" id="healthdot"></span>
 <span id="health">Checking infrastructure</span></div>
</header>

<div class="grid">
<section class="panel">
<h2>BGP Security — Animated AS Topology</h2>
<div class="muted">Green: legitimate path · Red: leaked path</div>
<svg viewBox="0 0 540 270">
 <path id="legit" class="route legit flow"
   d="M55 135 L170 50 L290 135 L465 135"/>
 <path id="leak" class="route leak"
   d="M55 135 L170 220 L290 135"/>
 <g>
  <rect class="node" x="15" y="116" width="80" height="38" rx="8"/>
  <text class="node-label" x="55" y="139">Observer</text>
  <rect class="node" x="130" y="31" width="80" height="38" rx="8"/>
  <text class="node-label" x="170" y="54">Provider</text>
  <rect class="node" x="130" y="201" width="80" height="38" rx="8"/>
  <text class="node-label" x="170" y="224">Leaker</text>
  <rect class="node" x="250" y="116" width="80" height="38" rx="8"/>
  <text class="node-label" x="290" y="139">Validator</text>
  <rect class="node" x="425" y="116" width="80" height="38" rx="8"/>
  <text class="node-label" x="465" y="139">Victim</text>
 </g>
</svg>
<div class="muted">
Conceptual topology for explaining route selection.
Not a literal rendering of every live BGP adjacency.
</div>
</section>

<section class="panel">
<h2>Security State & Explanation</h2>
<div id="stage-title" style="font-size:20px;font-weight:700">
Normal Routing
</div>
<div class="decision" id="explanation">
Legitimate BGP path selected.
</div>
<div class="metric"><span>Validation</span><strong id="validation">PASS</strong></div>
<div class="metric"><span>ONOS Protection</span><strong id="protection">Inactive</strong></div>
<div class="metric"><span>FRR Route</span><strong id="route">Legitimate</strong></div>
<div class="muted" style="margin-top:12px">
Guided replay uses archived experiment behavior.
It does not issue ONOS or FRR commands.
</div>
</section>
</div>


<section class="panel" id="security-demo">
<h2>RONO Security Demonstration</h2>

<h3>1. Manual Stage Selection</h3>
<div class="controls" id="security-stage-buttons">
<button onclick="securityStage(0)">1. Normal</button>
<button onclick="securityStage(1)">2. Route Leak</button>
<button onclick="securityStage(2)">3. ONOS Block</button>
<button onclick="securityStage(3)">4. FRR Recovery</button>
<button onclick="securityStage(4)">5. Restore</button>
</div>

<h3 id="security-stage-title">
2. Stage 1 of 5 — Normal Routing
</h3>

<div class="security-values">
<div>
<span>Validator best AS path</span>
<strong id="security-aspath">65001 65003 65169</strong>
</div>
<div>
<span>Targeted victim traffic</span>
<strong id="security-delivery">Delivered</strong>
</div>
<div>
<span>ONOS drop rule</span>
<strong id="security-rule">Absent</strong>
</div>
</div>

<h3>3. Research Explanation</h3>

<div class="decision">
<strong>Why does this happen?</strong>
<p id="security-why"></p>
</div>

<div class="decision">
<strong>What does RONO demonstrate?</strong>
<p id="security-evidence"></p>
</div>

<div class="muted">
ARCHIVED GUIDED REPLAY — no live network changes.
Displayed AS paths are illustrative.
</div>
</section>


<div class="grid">
<section class="panel">
<h2>ASPA vs OTC — Archived Reproduction</h2>
<div class="muted">
Mean eligible-AS attacker attraction by adoption level.
100 trials per defense and level. Not live measurements.
</div>
<div id="bars"></div>
<div class="muted" id="graph-note"></div>
</section>

<section class="panel">
<h2>RONO Experimental Evidence</h2>
<div class="metric"><span>Matched ASPA–OTC pairs</span><strong>500</strong></div>
<div class="metric"><span>Guarded regression cases</span><strong>13/13</strong></div>
<div class="metric"><span>Deployment ablation</span><strong>18/18</strong></div>
<div class="metric"><span>ONOS–FRR recovery cycles</span><strong>5/5</strong></div>
<div class="muted" style="margin-top:12px">
Historical results. The fresh three-trial pilot is separate.
</div>
</section>
</div>

<section class="panel">
<h2>Read-Only Infrastructure Monitoring</h2>
<div class="muted" id="monitor">Checking ONOS and OVS...</div>
<div class="muted">
Infrastructure health does not prove that the security experiment
is currently running.
</div>
</section>

<footer>
RONO · BGP Security Research · Guided Replay + Archived Evidence ·
Local-only service on port 8504
</footer>
</main>

<script>
const $=id=>document.getElementById(id);
const stages=[
 ["Normal Routing",
  "The validator selects the legitimate BGP path.",
  "PASS","Inactive","Legitimate"],
 ["Route Leak",
  "The leaker advertises a route through an unauthorized provider relationship. The leaked route becomes preferred in the controlled experiment.",
  "INVALID PATH","Inactive","Leaked"],
 ["ONOS Blocking",
  "A targeted ONOS OpenFlow drop rule blocks the selected observer traffic while the leaked BGP route remains selected.",
  "INVALID PATH","Active","Leaked"],
 ["FRR Recovery",
  "FRR applies the scoped prefix and AS-path filter. The legitimate route becomes selected while ONOS protection remains installed.",
  "REJECTED","Active","Legitimate"],
 ["Baseline Restoration",
  "The export guard and original policy are restored, then temporary filtering and ONOS protection are removed and verified.",
  "PASS","Removed","Legitimate"]
];
function stage(n){
 const x=stages[n];
 $('stage-title').textContent=x[0];
 $('explanation').textContent=x[1];
 $('validation').textContent=x[2];
 $('protection').textContent=x[3];
 $('route').textContent=x[4];
 $('stage-note').textContent=
  'GUIDED REPLAY — archived behavior, no network changes.';
 $('legit').style.opacity=(n===1||n===2)?'.22':'1';
 $('leak').style.opacity=(n===0||n===4)?'.12':'1';
 $('leak').classList.toggle('flow',n===1);
 $('legit').classList.toggle('flow',n===0||n>=3);
 document.querySelectorAll('.controls button').forEach((b,i)=>{
  b.classList.toggle('active',i===n);
 });
}
async function loadGraph(){
 try{
  const r=await fetch('/api/results');
  const d=await r.json();
  if(d.status!=='OK')throw Error(d.message);
  const target=$('bars');
  target.replaceChildren();
  const max=Math.max(...d.rows.map(x=>x.value),1);
  for(const x of d.rows){
   const row=document.createElement('div');
   row.className='barrow';
   const name=document.createElement('span');
   name.textContent=(x.adoption*100).toFixed(0)+'% adoption';
   const track=document.createElement('div');
   track.className='bar';
   const fill=document.createElement('div');
   fill.className='fill';
   fill.style.width=(100*x.value/max)+'%';
   track.append(fill);
   const value=document.createElement('strong');
   value.textContent=x.value.toFixed(4)+'%';
   row.append(name,track,value);
   target.append(row);
  }
  $('graph-note').textContent=
   'ASPA and OTC means overlap at all five evaluated levels.';
 }catch(e){
  $('graph-note').textContent='Results unavailable: '+e.message;
 }
}
async function monitor(){
 try{
  const r=await fetch('/api/status');
  const d=await r.json();
  const parts=[
   'Security ONOS HTTP: '+(d.onos_http??'unavailable'),
   'OVS bridge: '+d.ovs_bridge,
   'Security containers: '+d.security_containers
  ];
  $('monitor').textContent=parts.join(' · ');
  const healthy=d.onos_http===200||d.onos_http===401;
  $('healthdot').style.background=healthy?'#50d998':'#f1c65d';
  $('health').textContent=healthy?'ONOS reachable':'Infrastructure pending';
 }catch(e){
  $('monitor').textContent='Monitoring unavailable: '+e.message;
  $('health').textContent='Monitoring unavailable';
  $('healthdot').style.background='#ee6870';
 }
}
/* Old stage initialization disabled */
loadGraph();
monitor();
setInterval(monitor,5000);

const securityDetails=[
 {
  name:"Normal Routing",
  path:"65001 65003 65169",
  delivery:"Delivered",
  rule:"Absent",
  why:"The validator selects the legitimate provider route.",
  evidence:"Baseline routing and packet delivery establish the reference state."
 },
 {
  name:"Route Leak",
  path:"65066 65003 65169",
  delivery:"Delivered",
  rule:"Absent",
  why:"The leaked route becomes preferred because of the configured local-preference policy.",
  evidence:"The local provider-set check identifies an invalid provider relationship."
 },
 {
  name:"ONOS Block",
  path:"65066 65003 65169",
  delivery:"Blocked",
  rule:"Installed",
  why:"ONOS applies targeted OpenFlow protection while the leaked BGP route remains selected.",
  evidence:"Archived tests verified blocked victim probes and increasing drop counters."
 },
 {
  name:"FRR Recovery",
  path:"65001 65003 65169",
  delivery:"Restored",
  rule:"Still installed",
  why:"FRR rejects the leaked route and selects the legitimate alternative.",
  evidence:"Five ONOS-FRR recovery cycles passed the recorded checks."
 },
 {
  name:"Baseline Restoration",
  path:"65001 65003 65169",
  delivery:"Delivered",
  rule:"Removed",
  why:"Temporary filtering and ONOS protection are removed after policy restoration.",
  evidence:"Cleanup and connectivity checks verify baseline restoration."
 }
];

function securityStage(n){
 const d=securityDetails[n];
 if(!d)return;

 document.getElementById("security-stage-title").textContent=
  "2. Stage "+(n+1)+" of 5 — "+d.name;

 document.getElementById("security-aspath").textContent=d.path;
 document.getElementById("security-delivery").textContent=d.delivery;
 document.getElementById("security-rule").textContent=d.rule;
 document.getElementById("security-why").textContent=d.why;
 document.getElementById("security-evidence").textContent=d.evidence;

 document.querySelectorAll(
  "#security-stage-buttons button"
 ).forEach((b,i)=>{
  b.classList.toggle("active",i===n);
 });

 // RONO_STAGE_SYNC_FIX
 // Update topology without relying on older stage wrappers.
 const legitimate=document.getElementById("legit");
 const leaked=document.getElementById("leak");

 legitimate.style.opacity=
   (n===1||n===2)?"0.18":"1";

 leaked.style.opacity=
   (n===0||n===4)?"0.10":"1";

 legitimate.classList.toggle(
   "flow",n===0||n===3||n===4
 );

 leaked.classList.toggle(
   "flow",n===1
 );

 // Synchronize the original security status panel.
 const titles=[
   "Normal Routing",
   "Route Leak",
   "ONOS Blocking",
   "FRR Recovery",
   "Baseline Restoration"
 ];

 const validation=[
   "PASS","INVALID PATH","INVALID PATH",
   "REJECTED","PASS"
 ];

 const protection=[
   "Inactive","Inactive","Active",
   "Active","Removed"
 ];

 const routes=[
   "Legitimate","Leaked","Leaked",
   "Legitimate","Legitimate"
 ];

 const ids={
   "stage-title":titles[n],
   "validation":validation[n],
   "protection":protection[n],
   "route":routes[n]
 };

 for(const [id,value] of Object.entries(ids)){
   const el=document.getElementById(id);
   if(el)el.textContent=value;
 }

 const explanation=document.getElementById(
   "explanation"
 );

 if(explanation){
   explanation.textContent=d.why;
 }
}

window.addEventListener("load", () => securityStage(0));
// RONO_JS_INIT_REPAIR
</script>
</body>
</html>'''

def command(*args):
    try:
        p = subprocess.run(
            args, capture_output=True, text=True, timeout=4
        )
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None

def results():
    if not CAMPAIGN.exists():
        return {"status": "UNAVAILABLE", "message": "Archived CSV missing"}
    with CAMPAIGN.open(newline="") as f:
        records = list(csv.DictReader(f))
    rows = []
    for r in records:
        if (
            r.get("scenario_cls") == "AccidentalRouteLeak"
            and r.get("AdoptingPolicyCls") == "ASPA"
            and r.get("BasePolicyCls") == "ROV"
            and r.get("in_adopting_asns") == "Any"
            and r.get("outcome_type") == "DATA"
            and r.get("as_group") == "all_wout_ixps"
            and r.get("outcome") == "ATTACKER_SUCCESS"
            and r.get("propagation_round") == "1"
        ):
            rows.append({
                "adoption": float(r["percent_adopt"]),
                "value": float(r["value"]),
            })
    rows.sort(key=lambda x: x["adoption"])
    return {"status": "OK", "rows": rows}

def status():
    code = None
    try:
        urllib.request.urlopen(
            "http://127.0.0.1:8183/onos/v1/devices", timeout=3
        )
        code = 200
    except urllib.error.HTTPError as e:
        code = e.code
    except (OSError, urllib.error.URLError):
        pass

    bridge = command("sudo", "-n", "ovs-vsctl", "br-exists", "br-rono-sec")
    containers = command(
        "docker", "ps", "--format", "{{.Names}}"
    )
    count = (
        sum("sec" in n.lower() for n in containers.splitlines())
        if containers is not None else None
    )
    return {
        "onos_http": code,
        "ovs_bridge": "PRESENT" if bridge is not None else "UNAVAILABLE",
        "security_containers": count,
    }

class Handler(BaseHTTPRequestHandler):
    def respond(self, code, data, kind):
        body = data.encode() if isinstance(data, str) else json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self.respond(200, HTML, "text/html; charset=utf-8")
        elif path == "/api/results":
            self.respond(200, results(), "application/json")
        elif path == "/api/status":
            self.respond(200, status(), "application/json")
        else:
            self.respond(404, {"error": "not found"}, "application/json")

if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"RONO Security Dashboard: http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()
