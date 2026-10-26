"""Frozen-policy live UDP pilot. All methods use the same adapter and contexts.
No learning, retry replacement, automatic model selection, or simulated fallback.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import statistics
import time

METHODS = ['fixed', 'fixed_split', 'ecmp', 'least_utilized', 'greedy_demand', 'ppo']


def baseline(method, demands, bg_path, bg_mbps, utilization):
    if method == 'fixed':
        return 0
    if method == 'fixed_split':
        return 2  # Flow 1 path 1; flow 2 path 0.
    if method == 'ecmp':
        # UDP protocol and the exact data tuples used by the live adapter.
        paths = []
        for s,sp,d,dp in [('10.54.0.1',43001,'10.54.0.3',5401),
                          ('10.54.0.2',43002,'10.54.0.4',5402)]:
            key=json.dumps([17,sorted([(s,sp),(d,dp)])],separators=(',',':'))
            paths.append(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8],'big')%2)
        return 2*paths[0]+paths[1]
    if method == 'least_utilized':
        # Same previous-trial telemetry exposed to PPO. Fixed flow order;
        # predicted allocations are added to avoid selecting independently.
        loads=[max(utilization[0],utilization[3]),max(utilization[1],utilization[5])]
    elif method == 'greedy_demand':
        # Stronger baseline uses the announced background context, also seen
        # by PPO; no probing of alternative actions or receiver outcomes.
        loads=[bg_mbps/20 if p==bg_path else 0 for p in (0,1)]
    else:
        raise ValueError(method)
    paths=[0,0]
    for i in sorted(range(2),key=lambda i:(-demands[i],i)):
        p=min(range(2),key=lambda p:(loads[p]+demands[i]/20,p))
        paths[i]=p
        loads[p]+=demands[i]/20
    return 2*paths[0]+paths[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',type=Path,required=True)
    p.add_argument('--seed',type=int,default=20261025)
    p.add_argument('--horizon',type=int,default=2)
    args=p.parse_args()
    if not 1<=args.horizon<=32:
        p.error('horizon must be 1..32')
    import torch
    import stable_baselines3 as sb3
    from stable_baselines3 import PPO
    from rl_env import LiveEnv
    from rl_core import ACTIONS
    torch.set_num_threads(1)
    model=PPO.load(args.model,device='cpu')
    model.policy.set_training_mode(False)
    folder=Path('results/21-cn-load-balancing')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ-evaluation')
    folder.mkdir(parents=True)
    order=list(METHODS)
    random.Random(args.seed).shuffle(order)
    config={'scope':'single-model integration pilot; no statistical superiority claim',
            'model':str(args.model),'model_sha256':hashlib.sha256(args.model.read_bytes()).hexdigest(),
            'weights_frozen':True,'seed':args.seed,'horizon':args.horizon,'order':order,
            'sb3':sb3.__version__,'torch':torch.__version__,
            'transport':'UDP; same fixed five-tuples for every method',
            'ecmp_note':'one tuple pair only; not a sweep over ECMP collisions',
            'reset':'fixed action0 warm-up per method; excluded from scored trials',
            'failure_policy':'stop on first error; preserve completed and failed records'}
    (folder/'config.json').write_text(json.dumps(config,indent=2))
    print('Evidence:',folder.resolve(),flush=True)
    print('Method order:',order,flush=True)
    rows=[]
    contexts={}
    current_method=None
    try:
        for method in order:
            current_method=method
            env=LiveEnv(evaluation=True,horizon=args.horizon)
            print('Adapter:',env.request('/health'),flush=True)
            obs,reset_info=env.reset(seed=args.seed)
            (folder/(method+'-reset.json')).write_text(json.dumps(reset_info,indent=2))
            for step in range(args.horizon):
                context={'demands':list(env.demands),'background_path':env.bg_path,'background_mbps':env.bg_mbps}
                if step in contexts and contexts[step]!=context:
                    raise RuntimeError('Workload mismatch across methods; comparison aborted')
                contexts[step]=context
                tick=time.perf_counter_ns()
                if method=='ppo':
                    with torch.no_grad():
                        action,_=model.predict(obs,deterministic=True)
                    action=int(action)
                else:
                    action=baseline(method,env.demands,env.bg_path,env.bg_mbps,env.utilization)
                decision_ms=(time.perf_counter_ns()-tick)/1e6
                tick=time.perf_counter_ns()
                obs,reward,terminated,truncated,info=env.step(action)
                step_seconds=(time.perf_counter_ns()-tick)/1e9
                if terminated or truncated!=(step==args.horizon-1):
                    raise RuntimeError('Unexpected episode boundary')
                rates=info['receiver_mbps'];offered=info['offered_mbps']
                row={'method':method,'scenario':step,'context':context,'action':action,
                     'paths':list(ACTIONS[action]),'reward':reward,'receiver_mbps':rates,
                     'controlled_sum_mbps':sum(rates[:2]),'all_flow_sum_mbps':sum(rates),
                     'mean_satisfaction':statistics.mean(min(g/d,1) for g,d in zip(rates,offered)),
                     'loss_percent':info['loss_percent'],'decision_ms':decision_ms,
                     'step_seconds':step_seconds,'evidence':info['evidence'],'status':'PASS'}
                rows.append(row)
                with (folder/'trials.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
                print(json.dumps(row),flush=True)
            env.close()
        summary=[]
        for method in METHODS:
            selected=[r for r in rows if r['method']==method]
            summary.append({'method':method,'attempted':len(selected),'passed':len(selected),
                            'mean_controlled_mbps':statistics.mean(r['controlled_sum_mbps'] for r in selected),
                            'mean_satisfaction':statistics.mean(r['mean_satisfaction'] for r in selected),
                            'mean_reward':statistics.mean(r['reward'] for r in selected)})
        result={'status':'EVALUATION_PILOT_PASS','summary':summary,'scenarios':contexts,
                'scope':'one frozen model, one seed, matched pilot workloads; not a completed repeated campaign',
                'note':'Last method UDP rules remain active. Keep raw loss and per-flow metrics for analysis.'}
        (folder/'summary.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2),flush=True)
    except Exception as exc:
        (folder/'error.json').write_text(json.dumps({'status':'ERROR','method':current_method,
            'error':str(exc),'completed_trials':len(rows)},indent=2))
        raise


if __name__=='__main__':
    main()
