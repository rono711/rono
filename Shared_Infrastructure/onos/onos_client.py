"""Project-scoped ONOS intents; no FRR configuration privileges."""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DEVICE = 'of:0000000000000175'
APP = 'org.onosproject.cli'
KEYS = ('rono-frr-demo-ab', 'rono-frr-demo-ba',
        'rono-frr-demo-ab-arp', 'rono-frr-demo-ba-arp')


def api(path, method='GET', data=None):
    root = os.environ.get('ONOS_URL', 'http://127.0.0.1:8181/onos/v1').rstrip('/')
    user = os.environ.get('ONOS_USER', 'onos')
    password = os.environ.get('ONOS_PASSWORD', 'rocks')
    headers = {'Accept': 'application/json',
               'Authorization': 'Basic ' + base64.b64encode((user+':'+password).encode()).decode()}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(root + path, method=method,
        data=json.dumps(data).encode() if data is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors='replace')
        raise RuntimeError(f'ONOS HTTP {error.code}: {detail[:500]}') from error


def owned_intents():
    intents = api('/intents').get('intents', [])
    return [i for i in intents if i.get('appId') == APP and i.get('key') in KEYS]


def payload(key, ingress, egress):
    arp = key.endswith('-arp')
    return {'type': 'PointToPointIntent', 'appId': APP, 'key': key,
            'priority': 41000 if arp else 200,
            'selector': {'criteria': [{'type': 'ETH_TYPE', 'ethType': '0x0806'}] if arp else []}, 'treatment': {'instructions': []},
            'ingressPoint': {'device': DEVICE, 'port': str(ingress)},
            'egressPoint': {'device': DEVICE, 'port': str(egress)}}


def apply(action):
    if action not in ('connect', 'disconnect'):
        raise ValueError('Action must be connect or disconnect')
    # Only project-owned keys may be touched. Never enable reactive forwarding.
    if action == 'connect':
        devices = api('/devices').get('devices', [])
        if not any(d.get('id') == DEVICE and d.get('available') is True for d in devices):
            raise RuntimeError('Dedicated FRR transit switch is not available in ONOS')
        ports = api('/devices/' + urllib.parse.quote(DEVICE, safe='') + '/ports').get('ports', [])
        enabled = {str(p.get('port')) for p in ports if p.get('isEnabled') is True}
        if not {'1', '2'} <= enabled:
            raise RuntimeError('Dedicated switch ports 1 and 2 are not enabled')
        apps = api('/applications').get('applications', [])
        if any(a.get('name') == 'org.onosproject.fwd' and a.get('state') == 'ACTIVE' for a in apps):
            raise RuntimeError('Reactive forwarding is active; disable it before this intent-isolation test')
        existing = {i.get('key'): i for i in owned_intents()}
        for key, ingress, egress in [(KEYS[0], 1, 2), (KEYS[1], 2, 1),
                                    (KEYS[2], 1, 2), (KEYS[3], 2, 1)]:
            item = existing.get(key)
            if item and item.get('state') == 'INSTALLED':
                continue
            if item and item.get('state') not in ('WITHDRAWN', 'FAILED'):
                raise RuntimeError('Intent is transitioning; wait and retry: ' + key)
            api('/intents', 'POST', payload(key, ingress, egress))
    else:
        for item in owned_intents():
            if item.get('state') != 'WITHDRAWN':
                api('/intents/' + urllib.parse.quote(APP, safe='') + '/' + urllib.parse.quote(item['key'], safe=''), 'DELETE')
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        items = owned_intents()
        if action == 'connect' and len(items) == len(KEYS) and all(i.get('state') == 'INSTALLED' for i in items):
            return {'action': action, 'status': 'PASS', 'intents': items}
        if action == 'disconnect' and all(i.get('state') == 'WITHDRAWN' for i in items):
            return {'action': action, 'status': 'PASS', 'intents': items}
        if action == 'connect' and any(i.get('state') == 'FAILED' for i in items):
            raise RuntimeError('ONOS intent compilation failed; inspect ONOS GUI and logs')
        time.sleep(.5)
    raise RuntimeError('ONOS intent state verification timeout')
