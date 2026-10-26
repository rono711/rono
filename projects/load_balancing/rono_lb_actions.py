#!/usr/bin/env python3
"""Joint path actuator for two predefined TCP flows on CN ONOS only.

Path 0: rls1-rls2-rls4. Path 1: rls1-rls3-rls4.
Examples: python3 rono_lb_actions.py --paths 0 1
          python3 rono_lb_actions.py --paths 0 0
This validates enforcement, not a learned policy. Requires sibling baseline
and ECMP modules. Higher-priority exact TCP rules override those baselines.
No global flow deletion or Docker privileges. Unselected middle-switch rules
can remain installed but are not chosen by the edge rules.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from rono_lb_baseline import api
from rono_lb_ecmp import FLOWS, signature

PRIORITY = 22000


def rules_for(flow, path):
    src, sport, dst, dport = flow
    if path not in (0, 1):
        raise ValueError("Path must be 0 or 1")
    middle = 2 + path
    # Submit downstream forwarding first; edge choices last. This is not an
    # atomic network transaction: transitions must be measured separately.
    plans = [(middle, 2, src, sport, dst, dport),
             (4, int(dst.split('.')[-1]), src, sport, dst, dport),
             (middle, 1, dst, dport, src, sport),
             (1, int(src.split('.')[-1]), dst, dport, src, sport),
             (1, 3+path, src, sport, dst, dport),
             (4, 1+path, dst, dport, src, sport)]
    return [{"deviceId": f"of:{0x5400+switch:016x}", "priority": PRIORITY,
             "tableId": 0, "timeout": 0, "isPermanent": True,
             "selector": {"criteria": [
                 {"type": "ETH_TYPE", "ethType": "0x0800"},
                 {"type": "IPV4_SRC", "ip": s+"/32"},
                 {"type": "IPV4_DST", "ip": d+"/32"},
                 {"type": "IP_PROTO", "protocol": 6},
                 {"type": "TCP_SRC", "tcpPort": sp},
                 {"type": "TCP_DST", "tcpPort": dp}]},
             "treatment": {"instructions": [{"type": "OUTPUT", "port": str(port)}]}}
            for switch,port,s,sp,d,dp in plans]


def wait_rules(rules):
    deadline = time.monotonic()+30
    while True:
        actual = api('/flows').get('flows', [])
        installed = {signature(f) for f in actual if f.get('state') == 'ADDED'}
        count = sum(signature(r) in installed for r in rules)
        if count == len(rules):
            return actual
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Only {count}/{len(rules)} action rules ADDED; partial application possible. Do not run traffic.')
        time.sleep(1)


def apply_paths(paths):
    if len(paths) != 2 or any(p not in (0,1) for p in paths):
        raise ValueError('Exactly two binary path choices required')
    devices = api('/devices').get('devices', [])
    active = {d['id'] for d in devices if d.get('available')}
    if not {f'of:{0x5400+n:016x}' for n in range(1,5)} <= active:
        raise RuntimeError('Four available CN devices required on port 8182')
    before = api('/flows').get('flows', [])
    rules = [r for f,p in zip(FLOWS, paths) for r in rules_for(f,p)]
    # Check downstream before changing edge selections.
    downstream = [r for index,r in enumerate(rules) if index % 6 < 4]
    edges = [r for index,r in enumerate(rules) if index % 6 >= 4]
    for group in (downstream, edges):
        api('/flows?appId=org.onosproject.cli', {'flows':group})
        wait_rules(group)
    after = wait_rules(rules)
    return before, after, rules


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--paths', nargs=2, required=True, type=int, choices=(0,1))
    args = parser.parse_args()
    folder = Path('results/21-cn-load-balancing') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ-action')
    folder.mkdir(parents=True, exist_ok=False)
    report = {'phase':'manual_action_validation_not_DRL', 'controller':'127.0.0.1:8182',
              'paths':args.paths, 'status':'ERROR', 'data_plane_test':'PENDING',
              'priority':PRIORITY,
              'note':'Priority-22000 overrides remain active. Do not label later traffic ECMP/fixed until removed.'}
    try:
        before,after,rules = apply_paths(args.paths)
        for name,data in [('flows-before',before),('flows-after',after),('requested-rules',rules)]:
            (folder/(name+'.json')).write_text(json.dumps(data,indent=2))
        report.update(status='ACTION_RULES_PASS', verified_rules=len(rules))
    except Exception as exc:
        report['error'] = str(exc)
    (folder/'summary.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    print('Evidence:',folder.resolve())
    return 0 if report['status']=='ACTION_RULES_PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
