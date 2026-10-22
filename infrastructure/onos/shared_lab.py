"""Shared ONOS service and isolated FRR/DDS integration lab. Python standard library."""
import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import onos_client

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
LABEL = 'io.rono.shared-sdn'
ONOS = 'rono-onos-shared'
BRIDGE = 'br-rono-sdn'
EDGES = ('rono-sdn-frr-a', 'rono-sdn-frr-b')
DDS = 'rono-onos-dds'
IMAGE = 'onosproject/onos:2.7-latest'
FRR = 'quay.io/frrouting/frr:10.6.1'
RUN_DDS = '''set -e
source /opt/fastdds-python/install/setup.bash
export PYTHONPATH="/opt/fastdds-python/build/fastdds_python_examples/HelloWorldExample/generated_code:/app:${PYTHONPATH:-}"
exec /usr/bin/python3 -u /app/dds_onos.py "$@"
'''


def run(*args, check=True, timeout=30):
    result = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('Command failed: '+ ' '.join(args[:5]) + '\n' + result.stdout + result.stderr)
    return result


def docker(*args, **kw): return run('docker', *args, **kw)


def inspect(name):
    r=docker('inspect',name,check=False)
    if not r.returncode:
        item=json.loads(r.stdout)[0]
        if (item['Config'].get('Labels') or {}).get(LABEL)!='true':
            raise RuntimeError('Name is owned by another application: '+name)
        return item
    if 'no such' not in r.stderr.lower(): raise RuntimeError(r.stderr)
    return None


def wait_until(fn, seconds, description):
    deadline=time.monotonic()+seconds
    last=''
    tick=0
    while time.monotonic()<deadline:
        try:
            value=fn()
            if value: return value
        except Exception as error: last=str(error)
        if time.monotonic()>tick:
            print('Waiting for '+description+' ...',flush=True);tick=time.monotonic()+10
        time.sleep(1)
    raise RuntimeError('Timed out waiting for '+description+': '+last)


def onos_up():
    docker('info')
    item=inspect(ONOS)
    if item is None:
        subprocess.run(['docker','pull',IMAGE],check=True)
        docker('run','-d','--name',ONOS,'--label',LABEL+'=true',
            '--restart','unless-stopped','--memory','4g','--cpus','2',
            '-p','127.0.0.1:8181:8181','-p','127.0.0.1:6653:6653',
            '-p','127.0.0.1:8101:8101','-e','ONOS_APPS=drivers,openflow',IMAGE)
    elif not item['State']['Running']: docker('start',ONOS)
    wait_until(lambda:onos_client.api('/devices'),240,'ONOS REST API')
    for app in ('org.onosproject.drivers','org.onosproject.openflow'):
        onos_client.api('/applications/'+app+'/active','POST')
    print('ONOS GUI: http://127.0.0.1:8181/onos/ui',flush=True)
    return {'onos':onos_client.api('/devices'),'image_id':inspect(ONOS)['Image'],
        'image_metadata':json.loads(docker('image','inspect',inspect(ONOS)['Image']).stdout)[0].get('RepoDigests',[])}


def deps():
    if os.geteuid()==0: raise RuntimeError('Run as ubuntu, not root; the script uses sudo only where required')
    subprocess.run(['sudo','apt-get','update'],check=True)
    subprocess.run(['sudo','apt-get','install','-y','openvswitch-switch','iproute2','util-linux'],check=True)
    run('sudo','systemctl','enable','--now','openvswitch-switch',timeout=60)
    return {'ovs':run('sudo','ovs-vsctl','--version').stdout}


def bridge_owned():
    r=run('sudo','ovs-vsctl','br-exists',BRIDGE,check=False)
    if r.returncode==2: return False
    if r.returncode: raise RuntimeError(r.stderr)
    label=run('sudo','ovs-vsctl','get','bridge',BRIDGE,'external_ids:rono-owner').stdout.strip().strip('"')
    if label!='shared-sdn-v1': raise RuntimeError('Bridge name belongs to another application')
    return True


def frr_config(index):
    a,b=(1,2) if index==0 else (2,1)
    directory=HERE/'config'/('frr-a' if index==0 else 'frr-b')
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'daemons').write_text('zebra=yes\nbgpd=yes\nstaticd=no\nvtysh_enable=yes\nzebra_options=" -A 127.0.0.1 -s 90000000"\nbgpd_options=" -A 127.0.0.1"\n')
    (directory/'vtysh.conf').touch()
    (directory/'frr.conf').write_text(f'''frr defaults traditional
hostname sdn-frr-{a}
service integrated-vtysh-config
interface eth1
 ip address 198.18.0.{a}/30
exit
interface lo
 ip address 198.19.{a}.1/24
exit
router bgp {65100+a}
 bgp router-id 198.19.{a}.1
 no bgp ebgp-requires-policy
 neighbor 198.18.0.{b} remote-as {65100+b}
 neighbor 198.18.0.{b} timers 3 9
 address-family ipv4 unicast
  network 198.19.{a}.0/24
 exit-address-family
exit
''')
    return directory


def frr_ready(name):
    result = docker('exec',name,'vtysh','-c','show bgp ipv4 unicast summary json',check=False)
    if result.returncode:
        return False
    try:
        return isinstance(json.loads(result.stdout),dict)
    except ValueError:
        return False


def lab_up():
    if shutil.which('ovs-vsctl') is None: raise RuntimeError('Run deps first')
    onos_client.api('/devices')
    if not bridge_owned():
        run('sudo','ovs-vsctl','add-br',BRIDGE,'--','set','bridge',BRIDGE,
            'external_ids:rono-owner=shared-sdn-v1')
    run('sudo','ovs-vsctl','set','bridge',BRIDGE,'protocols=OpenFlow13',
        'other-config:datapath-id=0000000000000175','other-config:disable-in-band=true')
    run('sudo','ovs-vsctl','set-fail-mode',BRIDGE,'secure')
    run('sudo','ovs-vsctl','set-controller',BRIDGE,'tcp:127.0.0.1:6653')
    run('sudo','ip','link','set',BRIDGE,'up')
    docker('image','inspect',FRR)
    for i,name in enumerate(EDGES):
        config=frr_config(i)
        item=inspect(name)
        if item is None:
            docker('run','-d','--name',name,'--label',LABEL+'=true','--network','none',
                '--cap-add','NET_ADMIN','--cap-add','SYS_ADMIN','--sysctl','net.ipv4.ip_forward=1',
                '-v',str(config)+':/etc/frr',FRR)
        elif not item['State']['Running']:
            docker('start',name)
        pid=str(inspect(name)['State']['Pid'])
        port='ronosdn'+('a' if i==0 else 'b')
        # Recreate only this owned lab's veth after a container restart.
        if run('sudo','nsenter','-t',pid,'-n','ip','link','show','eth1',check=False).returncode:
            run('sudo','ovs-vsctl','--if-exists','del-port',BRIDGE,port)
            run('sudo','ip','link','del',port,check=False)
            run('sudo','ip','link','add',port,'type','veth','peer','name',port+'p')
            run('sudo','ip','link','set',port+'p','netns',pid)
            run('sudo','nsenter','-t',pid,'-n','ip','link','set',port+'p','name','eth1')
        run('sudo','ip','link','set',port,'up')
        run('sudo','nsenter','-t',pid,'-n','ip','link','set','eth1','up')
        run('sudo','nsenter','-t',pid,'-n','ip','addr','replace',f'198.18.0.{i+1}/30','dev','eth1')
        run('sudo','nsenter','-t',pid,'-n','ip','addr','replace',f'198.19.{i+1}.1/24','dev','lo')
        run('sudo','ovs-vsctl','--may-exist','add-port',BRIDGE,port,'--','set','interface',port,'ofport_request='+str(i+1))
        # Interface attachment occurs after daemon startup; reload the saved lab configuration.
        wait_until(lambda:frr_ready(name),45,name+' bgpd')
        loaded=docker('exec',name,'vtysh','-f','/etc/frr/frr.conf')
        if '%' in loaded.stdout+loaded.stderr: raise RuntimeError(loaded.stdout+loaded.stderr)
    wait_until(lambda:any(x.get('id')==onos_client.DEVICE and x.get('available') is True
        for x in onos_client.api('/devices').get('devices',[])),60,'OpenFlow switch')
    onos_client.api('/network/configuration','POST',{'devices':{onos_client.DEVICE:{'basic':{'name':'RONO FRR transit','driver':'ovs'}}}})
    return status()


def frr_snapshot():
    snapshots={}
    for name in EDGES:
        if inspect(name) is not None:
            r=docker('exec',name,'vtysh','-c','show bgp ipv4 unicast summary json',check=False)
            try: snapshots[name]=json.loads(r.stdout)
            except ValueError: snapshots[name]={'error':r.stderr or r.stdout}
    return snapshots


def status():
    return {'onos_rest_probe':onos_client.api('/devices'),'devices':onos_client.api('/devices'),
            'owned_intents':onos_client.owned_intents(),'frr':frr_snapshot(),
            'onos_dashboard':'http://127.0.0.1:8181/onos/ui',
            'rono_dashboard':'http://127.0.0.1:8502',
            'scope':'Dedicated ONOS-controlled transit and FRR pair; existing bgp7 unchanged'}


def verify(expected):
    peer='198.18.0.2'
    def observation():
        summary=frr_snapshot()
        router=summary.get(EDGES[0],{})
        family=router.get('ipv4Unicast',router)
        state=family.get('peers',{}).get(peer,{}).get('state')
        if expected=='connected' and state=='Established':
            data=json.loads(docker('exec',EDGES[0],'vtysh','-c','show bgp ipv4 unicast 198.19.2.0/24 json').stdout)
            if not any(p.get('valid') is True and p.get('bestpath',{}).get('overall') is True for p in data.get('paths',[])):
                return False
            ping=docker('exec',EDGES[0],'ping','-I','198.19.1.1','-c','2','-W','2','198.19.2.1',check=False)
            if ping.returncode==0: return {'status':'PASS','expected':expected,'bgp':summary,'route':data,'ping':ping.stdout}
        if expected=='disconnected' and state is not None and state!='Established':
            ping=docker('exec',EDGES[0],'ping','-I','198.19.1.1','-c','1','-W','1','198.19.2.1',check=False)
            if ping.returncode!=0:return {'status':'PASS','expected':expected,'bgp':summary,'ping':ping.stdout}
        return False
    return wait_until(observation,180,'BGP and data-plane '+expected)


def dds_start():
    item=inspect(DDS)
    if item is not None:
        if item['State']['Running']:return {'status':'RUNNING','container':DDS}
        docker('rm',DDS)
    args=['run','-d','--name',DDS,'--label',LABEL+'=true','--network','host','--ipc','host',
        '-v',str(HERE)+':/app:ro']
    env=dict(os.environ)
    # Forward custom credentials by variable name, not CLI value.
    for key in ('ONOS_URL','ONOS_USER','ONOS_PASSWORD'):
        if key in os.environ:args+=['-e',key]
    args+=['rono/policy-python:0.1','bash','-c',RUN_DDS,'rono','receiver']
    result=subprocess.run(['docker',*args],capture_output=True,text=True,timeout=30,env=env)
    if result.returncode:raise RuntimeError(result.stderr)
    def ready():
        item=inspect(DDS)
        logs=docker('logs',DDS)
        if not item['State']['Running']:raise RuntimeError(logs.stdout+logs.stderr)
        return '"event": "onos_dds_ready"' in logs.stdout
    wait_until(ready,30,'DDS adapter')
    return {'status':'READY','domain':175,'container':DDS}


def dds_send(action):
    dds_start()
    r=docker('run','--rm','--network','host','--ipc','host','-v',str(HERE)+':/app:ro',
        'rono/policy-python:0.1','bash','-c',RUN_DDS,'rono','sender','--action',action)
    lines=[json.loads(s) for s in r.stdout.splitlines() if s.startswith('{')]
    publication=next((s for s in lines if s.get('event')=='onos_dds_published'),None)
    if not publication:raise RuntimeError('DDS sender did not record publication: '+r.stdout+r.stderr)
    rid=publication['request_id']
    def applied():
        logs=docker('logs',DDS,'--tail','100').stdout
        for line in logs.splitlines():
            try: item=json.loads(line)
            except ValueError:continue
            if item.get('request_id')==rid and item.get('event')=='onos_intent_applied':return item
        return False
    return wait_until(applied,45,'matching DDS intent application')


def lab_down():
    # Controller service remains available for other projects.
    onos_client.apply('disconnect')
    item=inspect(DDS)
    if item:docker('stop','-t','35',DDS,timeout=45);docker('rm',DDS)
    for name in EDGES:
        if inspect(name):docker('rm','-f',name)
    if bridge_owned():run('sudo','ovs-vsctl','del-br',BRIDGE)
    for port in ('ronosdna','ronosdnb'):run('sudo','ip','link','del',port,check=False)
    return {'status':'STOPPED','onos_shared_service':'left running'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['deps','onos-up','lab-up','status','connect','disconnect',
        'verify-connected','verify-disconnected','dds-start','dds-connect','dds-disconnect','lab-down'])
    args=parser.parse_args()
    os.umask(0o022)
    folder=ROOT/'results/20-onos-integration'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder.mkdir(parents=True,exist_ok=True)
    lockdir=Path.home()/'.cache/rono';lockdir.mkdir(parents=True,exist_ok=True)
    try:
        with (lockdir/'shared-sdn.lock').open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            commands={'deps':deps,'onos-up':onos_up,'lab-up':lab_up,'status':status,
                'connect':lambda:onos_client.apply('connect'),
                'disconnect':lambda:onos_client.apply('disconnect'),
                'verify-connected':lambda:verify('connected'),
                'verify-disconnected':lambda:verify('disconnected'),
                'dds-start':dds_start,'dds-connect':lambda:dds_send('connect'),
                'dds-disconnect':lambda:dds_send('disconnect'),'lab-down':lab_down}
            result=commands[args.command]()
            (folder/'result.json').write_text(json.dumps(result,indent=2))
            print(json.dumps(result,indent=2));print('Evidence:',folder)
    except Exception as error:
        (folder/'error.json').write_text(json.dumps({'command':args.command,'error':str(error)},indent=2))
        print('ERROR:',error,file=sys.stderr);print('Evidence:',folder,file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
