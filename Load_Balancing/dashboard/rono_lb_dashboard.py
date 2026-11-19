#!/usr/bin/env python3
"""RONO load-balancing read-only dashboard. Python standard library only."""
import csv
import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(os.environ.get('RONO_WORKSPACE', str(Path.home() / 'RONO/Unified_Workspace')))
RESULTS = ROOT / 'Load_Balancing/analysis/final_results/six_strategy_summary.csv'
HOST = os.environ.get('RONO_LB_HOST', '127.0.0.1')
PORT = int(os.environ.get('RONO_LB_PORT', '8503'))
SWITCHES = ('rls1', 'rls2', 'rls3', 'rls4')
CACHE = {'at': 0.0, 'rates': {}, 'prev': {}, 'ts': 0.0}

HTML = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>RONO | Intelligent Traffic Balancing</title><style>
:root{color-scheme:dark;--bg:#0e1916;--panel:#1b3029;--mint:#50d998;--gold:#f1c65d;--red:#ee6870;--white:#f4f8f5;--muted:#b4cec1}*{box-sizing:border-box}body{margin:0;background:#09120f;color:var(--white);font:15px system-ui,-apple-system,Segoe UI,sans-serif}.shell{max-width:1040px;margin:24px auto;padding:22px;border-radius:24px;background:var(--bg)}header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #315043;padding-bottom:22px}.brand{display:inline-flex;flex-direction:column;align-items:flex-start;max-width:100%}.logo{font-weight:850;font-size:48px;letter-spacing:-3px;line-height:1}.logo .r{color:#ec515d}.logo .g{color:#43cc8b}.tagline{font-size:clamp(9px,1.6vw,12px);letter-spacing:-.15px;white-space:nowrap;color:#d3e9dd;margin-top:7px;max-width:100%}.sectiontop{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:28px 0 14px}h2{font-size:17px;margin:0}.statusline{font-size:12px;color:#c6ddd0;display:flex;align-items:center;gap:7px}.dot{height:12px;width:12px;border-radius:50%;display:inline-block;background:#87948e}.dot.green{background:var(--mint)}.dot.yellow{background:var(--gold)}.dot.red{background:var(--red)}.panel{background:var(--panel);border-radius:19px;padding:18px 16px 14px}.topology{display:block;width:100%;height:auto;max-height:430px}.route{fill:none;stroke-linecap:round;transition:stroke-width .6s,opacity .6s,stroke .6s}.route.upper{stroke:var(--mint);stroke-width:13}.route.lower{stroke:var(--gold);stroke-width:10;stroke-dasharray:14 13}.route.dim{opacity:.25;stroke-width:5}.switch{fill:#29463b;stroke:#8bbfa5;stroke-width:2}.switch-label{fill:white;font-size:21px;text-anchor:middle;dominant-baseline:middle}.pathlabel{font-size:18px;text-anchor:middle;fill:#a5e1c0}.pathlabel.bottom{fill:#f3d58d}.packet{fill:white;filter:drop-shadow(0 0 5px white)}.packet.bottom{fill:var(--gold);filter:drop-shadow(0 0 5px #f1c65d)}.packet.off{display:none}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:4px}.metric .name{font-size:13px;color:var(--muted)}.metric strong{font-size:26px;color:var(--mint)}.metric.alt strong{color:var(--gold)}.track{height:8px;border-radius:10px;background:#355147;overflow:hidden;margin-top:5px}.fill{height:100%;border-radius:10px;background:var(--mint);width:0;transition:width .8s}.alt .fill{background:var(--gold)}.lights{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin-top:13px}.light{background:var(--panel);border-radius:14px;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:5px;padding:12px 5px;font-size:12px;font-weight:700}.light .dot{height:20px;width:20px}.subpanel{background:#14251f;border:1px solid #2b483c;border-radius:16px;padding:16px;margin-top:16px}.subpanel h3{margin:0 0 8px;font-size:15px}.muted{color:var(--muted);font-size:12px;line-height:1.6}.bars{display:grid;gap:8px;margin-top:12px}.barrow{display:grid;grid-template-columns:125px 1fr 85px;align-items:center;gap:10px;font-size:12px}.barrow .track{margin:0;height:11px}.barrow .fill{background:#52c98d}.barrow.highlight .fill{background:#f0c45d}.barrow.ppo .fill{background:#e46a6d}.controls{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}button{background:#285c48;color:white;border:1px solid #488c6b;padding:9px 13px;border-radius:9px;cursor:pointer}button:hover{background:#33785b}button.active{background:#dfeee4;color:#163c2b}footer{font-size:11px;color:#94b6a5;margin-top:18px}code{color:#b8e9cb}#health{font-size:12px;color:#c7ded2}#message{color:#eecf8c;font-size:12px}@media(max-width:560px){.shell{margin:0;border-radius:0;padding:14px}.logo{font-size:42px}.tagline{font-size:9px}.barrow{grid-template-columns:94px 1fr 65px}.switch-label{font-size:20px}.metrics{gap:8px}}
@media(prefers-reduced-motion:reduce){.packet{display:none}.route,.fill{transition:none}}
</style></head><body><main class="shell"><header><div class="brand"><div class="logo"><span class="r">RO</span><span class="g">NO</span></div><div class="tagline">Intelligent Routing · Balanced Traffic · Reliable Networks</div></div><svg width="34" height="34" viewBox="0 0 40 40" aria-hidden="true"><g fill="none" stroke="#53d79a" stroke-width="2"><path d="M20 7L9 28M20 7L31 28M9 28H31"/><circle cx="20" cy="7" r="3"/><circle cx="9" cy="28" r="3"/><circle cx="31" cy="28" r="3"/></g></svg></header>
<div class="sectiontop"><h2>LIVE TRAFFIC DISTRIBUTION</h2><div class="statusline"><span class="dot" id="healthdot"></span><span id="health">Checking telemetry…</span></div></div>
<section class="panel"><svg class="topology" viewBox="0 0 640 370" role="img" aria-label="Four-switch diamond topology, two network paths"><defs><path id="route0" d="M105 185 L320 62 L535 185"/><path id="route1" d="M105 185 L320 306 L535 185"/></defs><text x="320" y="19" class="pathlabel">PATH 0</text><text x="320" y="360" class="pathlabel bottom">PATH 1</text><use href="#route0" class="route upper" id="upper"/><use href="#route1" class="route lower" id="lower"/><circle r="9" class="packet" id="p0"><animateMotion dur="2.3s" repeatCount="indefinite"><mpath href="#route0"/></animateMotion></circle><circle r="9" class="packet bottom" id="p1"><animateMotion dur="2.8s" repeatCount="indefinite"><mpath href="#route1"/></animateMotion></circle><g><rect class="switch" x="57" y="155" rx="15" width="96" height="60"/><text class="switch-label" x="105" y="185">rls1</text><rect class="switch" x="272" y="32" rx="15" width="96" height="60"/><text class="switch-label" x="320" y="62">rls2</text><rect class="switch" x="272" y="276" rx="15" width="96" height="60"/><text class="switch-label" x="320" y="306">rls3</text><rect class="switch" x="487" y="155" rx="15" width="96" height="60"/><text class="switch-label" x="535" y="185">rls4</text></g></svg>
<div class="metrics"><div class="metric"><div class="name">Path 0 utilization</div><strong id="u0">—</strong><div class="track"><div class="fill" id="f0"></div></div></div><div class="metric alt"><div class="name">Path 1 utilization</div><strong id="u1">—</strong><div class="track"><div class="fill" id="f1"></div></div></div></div><div class="muted" style="margin-top:9px" id="telemetrynote">Waiting for two OVS counter snapshots. No simulated live values.</div></section>
<div class="lights"><div class="light"><span class="dot green"></span>Balanced</div><div class="light"><span class="dot yellow"></span>Congested</div><div class="light"><span class="dot red"></span>Overloaded</div></div>
<section class="subpanel"><h3>Path animation</h3><div class="muted">Live mode follows measured traffic on rls1 egress ports 3 and 4. Manual modes are visual previews only and never change network rules.</div><div class="controls"><button id="live" class="active" onclick="setMode('live')">Live telemetry</button><button id="both" onclick="setMode('both')">Preview both paths</button><button id="only0" onclick="setMode('only0')">Preview path 0</button><button id="only1" onclick="setMode('only1')">Preview path 1</button></div><div id="message">Read-only dashboard: no path control commands are sent.</div></section>
<section class="subpanel"><h3>ONOS / OVS status</h3><div id="controller" class="muted">Checking…</div><div id="switches" class="muted"></div></section>
<section class="subpanel"><h3>ARCHIVED EXPERIMENTAL COMPARISON</h3><div class="muted">Mean controlled throughput (Mbps) from two complete six-strategy pilots. These are archived measurements, not live traffic readings or proof of PPO superiority.</div><div class="bars" id="bars"></div><div class="muted" id="archiveinfo" style="margin-top:10px"></div></section>
<footer>RONO · Routing Orchestration and Network Optimization · Computer Networks research demo · Refresh every 3 seconds · Local-only HTTP service</footer></main><script>
let mode='live',last=null;const $=id=>document.getElementById(id);function setMode(m){mode=m;for(const b of document.querySelectorAll('.controls button'))b.classList.toggle('active',b.id===m);$('message').textContent=m==='live'?'LIVE: animation follows measured OVS egress activity.':'PREVIEW ONLY: this animation does not change OpenFlow routes.';render()}function render(){let a=false,b=false;if(mode==='both'){a=b=true}else if(mode==='only0'){a=true}else if(mode==='only1'){b=true}else if(last?.telemetry?.status==='OK'){a=(last.telemetry.path0_mbps||0)>0.25;b=(last.telemetry.path1_mbps||0)>0.25} $('upper').classList.toggle('dim',!a);$('lower').classList.toggle('dim',!b);$('p0').classList.toggle('off',!a);$('p1').classList.toggle('off',!b)}function safe(v){return Number.isFinite(v)?v.toFixed(1)+'%':'—'}async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);last=await r.json();const t=last.telemetry;let pct0=t.status==='OK'?t.path0_mbps/20*100:NaN,pct1=t.status==='OK'?t.path1_mbps/20*100:NaN;$('u0').textContent=safe(pct0);$('u1').textContent=safe(pct1);$('f0').style.width=Number.isFinite(pct0)?Math.min(100,Math.max(0,pct0))+'%':'0%';$('f1').style.width=Number.isFinite(pct1)?Math.min(100,Math.max(0,pct1))+'%':'0%';$('telemetrynote').textContent=t.status==='OK'?'Observed OVS TX counters · 20 Mbps nominal capacity per path · '+new Date(t.timestamp*1000).toLocaleTimeString():'Telemetry unavailable: '+t.message;const healthy=last.controller.http_status===401||last.controller.http_status===200;const all=Object.values(last.switches).every(v=>v===true);$('controller').textContent='ONOS REST HTTP: '+(last.controller.http_status||'unavailable')+' · OpenFlow connected: '+Object.values(last.switches).filter(Boolean).length+'/4';$('switches').textContent=Object.entries(last.switches).map(([k,v])=>k+': '+(v===true?'connected':v===false?'disconnected':'unknown')).join(' · ');let severity=!healthy||!all?'red':t.status!=='OK'?'gray':Math.max(pct0,pct1)>=100?'red':Math.max(pct0,pct1)>=80?'yellow':'green';$('healthdot').className='dot '+severity;$('health').textContent=severity==='green'?'Healthy':severity==='yellow'?'High utilization':severity==='red'?'Critical / disconnected':'Telemetry pending';render()}catch(e){$('healthdot').className='dot';$('health').textContent='Monitoring unavailable';$('telemetrynote').textContent=String(e);last=null;render()}}async function archive(){try{const r=await fetch('/api/archive');const d=await r.json();if(d.status!=='OK')throw Error(d.message);const max=Math.max(...d.rows.map(x=>x.mean_controlled_mbps));$('bars').replaceChildren();for(const x of [...d.rows].sort((a,b)=>b.mean_controlled_mbps-a.mean_controlled_mbps)){const row=document.createElement('div');row.className='barrow '+(x.method==='greedy_demand'?'highlight':x.method==='ppo'?'ppo':'');const label=document.createElement('span');label.textContent=x.method.replaceAll('_',' ');const track=document.createElement('div');track.className='track';const fill=document.createElement('div');fill.className='fill';fill.style.width=(100*x.mean_controlled_mbps/max)+'%';track.append(fill);const value=document.createElement('b');value.textContent=x.mean_controlled_mbps.toFixed(3)+' Mbps';row.append(label,track,value);$('bars').append(row)}$('archiveinfo').textContent=d.rows.length+' strategies · historical pilot averages · no inferential significance claim.'}catch(e){$('archiveinfo').textContent='Archived summary unavailable: '+e.message}}refresh();archive();setInterval(refresh,3000);</script></body></html>'''

def command(*args, timeout=3):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None

def get_counter(port):
    output = command('sudo', '-n', 'ovs-ofctl', '-O', 'OpenFlow13', 'dump-ports', 'rls1', str(port))
    if not output:
        return None
    import re
    # OpenFlow dump-ports format: 'port  3: ...\n rx ...\n tx pkts=..., bytes=...'
    match = re.search(r'\btx\s+pkts=\d+,\s*bytes=(\d+)', output)
    return int(match.group(1)) if match else None

def status():
    now = time.monotonic()
    switches = {}
    for sw in SWITCHES:
        out = command('sudo', '-n', 'ovs-vsctl', '--if-exists', 'get', 'Controller', sw, 'is_connected')
        switches[sw] = True if out == 'true' else False if out == 'false' else None
    import urllib.request, urllib.error
    code = None
    try:
        with urllib.request.urlopen('http://127.0.0.1:8182/onos/v1/devices', timeout=2) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    except (urllib.error.URLError, TimeoutError):
        pass
    if now - CACHE['at'] >= 2:
        counts = {p: get_counter(p) for p in (3, 4)}
        previous, prev_ts = CACHE['prev'], CACHE['ts']
        rates = {}
        if prev_ts and now > prev_ts:
            for p in (3, 4):
                if counts[p] is not None and previous.get(p) is not None and counts[p] >= previous[p]:
                    rates[p] = 8 * (counts[p] - previous[p]) / (now - prev_ts) / 1e6
        CACHE.update(at=now, prev=counts, ts=now, rates=rates)
    rates = CACHE['rates']
    ok = len(rates) == 2
    return {'controller': {'http_status': code}, 'switches': switches,
            'telemetry': {'status': 'OK' if ok else 'UNAVAILABLE',
                          'path0_mbps': round(rates[3], 4) if ok else None,
                          'path1_mbps': round(rates[4], 4) if ok else None,
                          'timestamp': time.time(),
                          'message': 'Need two valid consecutive OpenFlow port snapshots' if not ok else 'Read-only OVS TX byte deltas'}}

class Handler(BaseHTTPRequestHandler):
    def reply(self, code, content, mime):
        body = content.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/':
            return self.reply(200, HTML, 'text/html; charset=utf-8')
        if path == '/api/status':
            return self.reply(200, json.dumps(status()), 'application/json')
        if path == '/api/archive':
            try:
                with RESULTS.open(newline='') as f:
                    rows = list(csv.DictReader(f))
                for row in rows:
                    row['mean_controlled_mbps'] = float(row['mean_controlled_mbps'])
                result = {'status':'OK','rows':rows}
            except (OSError, ValueError, KeyError) as e:
                result = {'status':'UNAVAILABLE','message':str(e)}
            return self.reply(200, json.dumps(result), 'application/json')
        self.reply(404, json.dumps({'error':'not found'}), 'application/json')
    def do_POST(self):
        self.reply(405, json.dumps({'error':'read-only dashboard'}), 'application/json')

if __name__ == '__main__':
    print(f'RONO LB dashboard listening on http://{HOST}:{PORT}/', flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
