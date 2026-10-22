"""Sequential, warm-endpoint application enforcement benchmark."""
import csv
import hmac
import importlib.util
import json
import math
import statistics
import threading
from datetime import datetime, timezone
from pathlib import Path


def summarize(rows):
    summary = []
    for transport in ('rest', 'dds'):
        for action in ('alternative', 'direct'):
            group = [r for r in rows if r['transport'] == transport and r['action'] == action]
            values = sorted(r['enforcement_ms'] for r in group if r['status'] == 'PASS')
            summary.append(dict(transport=transport, action=action, attempted=len(group),
                passed=len(values), failed=len(group)-len(values),
                median_ms=statistics.median(values) if values else None,
                p95_ms=values[max(0, math.ceil(.95*len(values))-1)] if values else None))
    return summary


def plot(summary):
    maximum = max([r['p95_ms'] or 0 for r in summary] + [1])
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="850" height="330" viewBox="0 0 850 330"><rect width="850" height="330" fill="white"/><g font-family="sans-serif" fill="#142039"><text x="20" y="28" font-size="18">Application enforcement delay (ms): median bar / p95 marker</text>']
    for i, row in enumerate(summary):
        y=65+i*58
        label=row['transport'].upper()+' '+row['action']
        parts.append(f'<text x="20" y="{y+18}">{label}</text>')
        if row['median_ms'] is not None:
            width=430*row['median_ms']/maximum
            x=210+430*row['p95_ms']/maximum
            color='#006A4E' if row['transport']=='dds' else '#3156a6'
            parts.append(f'<rect x="210" y="{y}" width="{width:.2f}" height="25" fill="{color}"/><path d="M{x:.2f},{y-3} v31" stroke="black" stroke-width="2"/><text x="665" y="{y+18}">{row["median_ms"]:.1f} / {row["p95_ms"]:.1f}</text>')
    return ''.join(parts)+'</g></svg>'


def install(base, app_file):
    root=Path(app_file).resolve().parents[1]
    state={'running':False,'progress':'Ready','summary':[], 'folder':'','error':''}
    gate=threading.RLock()
    parent=base.Handler

    def execute(cycles):
        folder=root/'results/19-enforcement-benchmark'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        folder.mkdir(parents=True)
        rows=[]
        state['folder']=str(folder)
        run=base._rono_run_path_intent
        spec=importlib.util.spec_from_file_location('benchmark_agent',root/'projects/integration/path_agent.py')
        agent=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent)
        def apply(action, transport):
            result=run(action,transport)
            outcome=json.loads(result.stdout)
            if result.returncode or outcome.get('status')!='PASS':
                raise RuntimeError(outcome.get('error','Agent verification failed'))
            return outcome
        try:
            agent.guard()
            state['progress']='Preparing persistent DDS endpoints; startup excluded'
            base._rono_prepare_dds()
            apply('direct','rest')
            state['progress']='Warm-up: four state changes, excluded from results'
            for transport in ('rest','dds'):
                for action in ('alternative','direct'):
                    apply(action,transport)
            for cycle in range(cycles):
                order=('rest','dds') if cycle%2==0 else ('dds','rest')
                for transport in order:
                    for action in ('alternative','direct'):
                        state['progress']=f'Cycle {cycle+1}/{cycles}: {transport} → {action}'
                        row=dict(cycle=cycle+1,transport=transport,action=action,status='ERROR',enforcement_ms='',agent_apply_ms='',request_id='',evidence='',error='')
                        try:
                            before=agent.routes()
                            if agent.selected(before,action):
                                raise RuntimeError('Target already selected: trial would be a no-op')
                            out=apply(action,transport)
                            row.update(status='PASS',enforcement_ms=out['enforcement_ms'],agent_apply_ms=out.get('observed_apply_ms',''),request_id=out['request_id'],evidence=out['transport_results'])
                        except Exception as error:
                            row['error']=str(error)
                            raise
                        finally:
                            rows.append(row)
                            with (folder/'trials.csv').open('w',newline='') as stream:
                                writer=csv.DictWriter(stream,fieldnames=list(row))
                                writer.writeheader();writer.writerows(rows)
            state['progress']='Trials complete'
        except Exception as error:
            state['error']=str(error)
            state['progress']='Stopped after error; inspect evidence'
        finally:
            try:
                state['restoration']=apply('restore','rest')['status']
            except Exception as error:
                state['restoration']='FAILED: '+str(error)
                state['error']+=' Baseline restoration failed; inspect R2 before continuing.'
            summary=summarize(rows)
            state['summary']=summary
            record=dict(state,cycles_requested=cycles,metric='Host monotonic handoff → agent subprocess completion (includes agent evidence I/O and route polling)',scope='Application stack comparison: DDS includes local HTTP gateway hops; not pure protocol latency. No traffic RTT or network-wide convergence measurement.',sampling='Nearest-rank p95; separate transport/action groups; first failure aborts; warm-ups excluded; agent route polling quantizes observations.')
            (folder/'summary.json').write_text(json.dumps(record,indent=2))
            (folder/'comparison.svg').write_text(plot(summary))
            with gate:
                state['running']=False

    class Handler(parent):
        def do_GET(self):
            if self.path=='/api/path-benchmark':
                with gate:
                    return self.reply(200,dict(state))
            if self.path=='/api/path-benchmark/plot':
                with gate:
                    path=Path(state['folder'])/'comparison.svg'
                    if state['running'] or not state['folder'] or not path.exists():
                        return self.reply(404,{'error':'No completed plot'})
                    payload=path.read_bytes()
                self.send_response(200);self.send_header('Content-Type','image/svg+xml');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
                return
            return super().do_GET()
        def do_POST(self):
            if self.path=='/api/path-benchmark':
                if not hmac.compare_digest(self.headers.get('X-RONO-Token',''),base.TOKEN):
                    return self.reply(403,{'error':'Invalid token'})
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=1024: raise ValueError('Invalid size')
                    data=json.loads(self.rfile.read(size))
                    cycles=data.get('cycles',5)
                    if type(cycles) is not int or not 1<=cycles<=50: raise ValueError('Cycles must be 1–50')
                    with gate:
                        if state['running'] or base._rono_path_busy():
                            return self.reply(409,{'error':'Another path operation is running'})
                        state.update(running=True,progress='Starting',summary=[],error='',folder='')
                        threading.Thread(target=execute,args=(cycles,),daemon=False).start()
                    return self.reply(202,{'message':'Benchmark started'})
                except Exception as error:
                    return self.reply(400,{'error':str(error)})
            with gate:
                if state['running'] and self.path!='/api/path-intents':
                    return self.reply(409,{'error':'Benchmark running; wait for completion'})
                return super().do_POST()
    base.Handler=Handler
    panel='''<section class="card"><h2>REST / DDS enforcement benchmark</h2>
<p>Persistent DDS endpoints. Same path agent and verification. Each cycle measures Alternative → Direct for both transports. Do not run CLI steering or the DDS BGP bridge during trials.</p>
<label>Cycles per transport <input id="bench-cycles" type="number" min="1" max="50" value="5"></label>
<button id="bench-start" onclick="startBenchmark()">Run benchmark</button>
<p>Start with 5 pilot cycles. After checking the results, use 30 cycles. This compares application stacks, including DDS gateway HTTP hops; it does not measure pure transport latency.</p>
<pre id="bench-status">Ready</pre><img id="bench-plot" style="display:none;width:100%;background:white" alt="Median and p95 application enforcement delay"></section>'''
    js='''<script>
async function startBenchmark(){try{const r=await fetch('/api/path-benchmark',{method:'POST',headers:{'Content-Type':'application/json','X-RONO-Token':token},body:JSON.stringify({cycles:Number(document.getElementById('bench-cycles').value)})});const d=await r.json();if(!r.ok)throw Error(d.error);await refreshBenchmark()}catch(e){document.getElementById('bench-status').textContent=e.message}}
async function refreshBenchmark(){try{const r=await fetch('/api/path-benchmark');const d=await r.json();document.getElementById('bench-status').textContent=JSON.stringify(d,null,2);document.getElementById('bench-start').disabled=d.running;const img=document.getElementById('bench-plot');if(!d.running&&d.summary.length){if(img.dataset.folder!==d.folder){img.src='/api/path-benchmark/plot?t='+Date.now();img.dataset.folder=d.folder}img.style.display='block'}else{img.style.display='none'}}catch(e){console.log(e)}}
setInterval(refreshBenchmark,3000);refreshBenchmark();</script>'''
    base.PAGE=base.PAGE.replace('</main>',panel+'</main>',1).replace('</html>',js+'</html>',1)
