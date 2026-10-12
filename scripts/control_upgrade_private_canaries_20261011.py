"""Disposable main-network canaries: fresh reference resolution, no app commands."""
import copy,hashlib,json,sys
from pathlib import Path
import control_upgrade_main_dns_20261011 as dns
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from plan_upgrade_owner_retirement_20261010 import listing

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage41/private-canaries-v1'
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:5b3e5fa46282d55ce615fc2770bca5e51047b8a592b188ce06795dc2f6f5a551'
API='?api-version=2026-07-01'
PREFIX='UPGRADE_SECRET_RESOLUTION '

def name(group):
    if type(group) is not int or not 3<=group<=17:raise ValueError('Outside exact main canary scope')
    return 'ibkrv2stg-netqa41-g'+str(group).zfill(2)

def resource(group):return dns.old.rid('Microsoft.App/jobs',name(group))

def probe(group,count):
    return """import json,os,socket,ssl
EXPECTED=%r
GROUP=%r
COUNT=%r
def main():
    for index in range(COUNT):
        if not os.environ.get('CHECK_SECRET_'+str(index).zfill(3)):
            raise ValueError('Required reference not resolved')
    peers=[]
    for host,expected in EXPECTED.items():
        addresses={x[4][0] for x in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)}
        if addresses!={expected}:raise ValueError('Private DNS mismatch')
        with socket.create_connection((host,443),timeout=15) as sock:
            with ssl.create_default_context().wrap_socket(sock,server_hostname=host) as tls:
                if tls.getpeername()[0]!=expected:raise ValueError('Private TLS mismatch')
        peers.append({'host':host,'private_ip':expected})
    print(%r+json.dumps({'passed':True,'group':GROUP,'resolved_reference_count':COUNT,'peers':peers,
        'secret_values_logged':False,'normal_application_started':False,'database_writes':0,'broker_calls':0}),flush=True)
try:main()
except Exception as error:
    print('UPGRADE_SECRET_RESOLUTION_ERROR '+type(error).__name__,flush=True)
    raise SystemExit(1)
""" % (dns.EXPECTED,group,count,PREFIX)

def body(group):
    number=group['group'];name(number)
    if not group['environment'].lower().endswith('/ibkrv2stg-env') or group['profile']!='Consumption':
        raise ValueError('Main Consumption environment only')
    if len(group['registries'])!=1 or group['registries'][0]['server']!='ibkrv2stgauhecwc4itaoo.azurecr.io':
        raise ValueError('Exact existing main registry required')
    registry=group['registries'][0]
    if registry.get('identity','').lower()!=group['identity'].lower() or registry.get('username') or registry.get('passwordSecretRef'):
        raise ValueError('No registry password transfer')
    refs=group['references']
    if len(refs)>5:raise ValueError('Unbounded secret references')
    secrets=[];env=[]
    for index,ref in enumerate(refs):
        alias='qa-ref-'+str(index).zfill(3)
        if ref['identity'].lower()!=group['identity'].lower():raise ValueError('Reference identity differs')
        secrets.append({'name':alias,'keyVaultUrl':ref['keyVaultUrl'],'identity':ref['identity']})
        env.append({'name':'CHECK_SECRET_'+str(index).zfill(3),'secretRef':alias})
    return {'location':'eastasia','tags':{'purpose':'upgrade-stage41-private-canary','temporary':'true'},
        'identity':{'type':'UserAssigned','userAssignedIdentities':{group['identity']:{}}},
        'properties':{'environmentId':group['environment'],'workloadProfileName':'Consumption',
            'configuration':{'triggerType':'Manual','replicaTimeout':300,'replicaRetryLimit':0,
                'manualTriggerConfig':{'replicaCompletionCount':1,'parallelism':1},'secrets':secrets,
                'registries':[{'server':registry['server'],'identity':registry['identity']}]},
            'template':{'containers':[{'name':'private-network-check','image':IMAGE,'command':['python'],
                'args':['-c',probe(number,len(refs))],'env':env,'resources':{'cpu':0.25,'memory':'0.5Gi'}}]}}}

def definitions():
    plan=protected(OUT/'plan.json')
    return plan,{name(g['group']):body(g) for g in plan['groups'] if g['group']>=3}

def same(actual,wanted):
    if not dns.old.contains_requested(actual,wanted):raise ValueError('Temporary canary definition changed')

def preserve(plan,wanted):
    items=dns.old.current_resources()
    originals=[]
    for item in items:
        if item['name'] in wanted:same(item,wanted[item['name']])
        else:originals.append(item)
    if dns.old.bindings(originals)!=plan['boundary']['workloads']:raise ValueError('Production workload changed')
    auth=dns.old.request('GET',dns.WEB+'/authConfigs/current'+API)
    if auth['properties']!=plan['boundary']['web_auth']:raise ValueError('Website authentication changed')
    dns.health()

def main(stage,group_text):
    group=int(group_text);target=resource(group);folder=OUT/name(group)
    plan,wanted=definitions();expected=wanted[name(group)]
    if stage=='remove':
        intent=protected(folder/'intent.json')
        if intent['body']!=expected:raise ValueError('Frozen canary scope changed')
        actual=dns.old.request('GET',target+API,missing=True)
        if actual is None:raise ValueError('Reconcile missing canary; do not repeat deletion')
        same(actual,expected)
        runs=listing(target+'/executions','2026-07-01')
        if any(r['properties']['status'] not in ('Succeeded','Failed','Stopped','Canceled','Cancelled') for r in runs):
            raise ValueError('Do not remove an active canary')
        details=[dns.old.request('GET',target+'/executions/'+r['name']+API) for r in runs]
        if any(s.get('value') for s in actual['properties']['configuration'].get('secrets',[])):
            raise ValueError('Never archive secret values')
        save(folder,'retirement-intent',{'definition':actual,'executions':details,'resource':target,'restore_body':expected})
        dns.old.request('DELETE',target+API)
        save(folder,'retirement-ack',{'resource':target,'delete_requested':True})
        print(json.dumps({'group':group,'retirement_requested':True}));return
    if stage not in ('create','start','read'):raise ValueError('Unknown action')
    preserve(plan,wanted);dns.links(True)
    if stage=='create':
        dns.old.runtime.safe_window()
        if folder.exists() or dns.old.request('GET',target+API,missing=True) is not None:
            raise ValueError('Preserve pre-existing resource or attempt')
        deployed=protected(ROOT/'.work/platform-upgrade-20261011-stage38/closeout.json')
        if IMAGE not in deployed['boundary']['workloads']['ibkrv2stg-report-snapshot']['images']:
            raise ValueError('Qualified installed image required')
        save(folder,'intent',{'resource':target,'body':expected,'existing_consumers':[g['workloads'] for g in plan['groups'] if g['group']==group][0],
            'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'credentials_copied':False,'secret_values_logged':False,'cold_pull_proof':False})
        ack=dns.old.request('PUT',target+API,data=expected)
        save(folder,'create-ack',{'id':ack.get('id',target),'state':ack.get('properties',{}).get('provisioningState')})
        print(json.dumps({'group':group,'create_requested':True}));return
    intent=protected(folder/'intent.json')
    if intent['body']!=expected or intent['source_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise ValueError('Frozen canary source changed')
    job=dns.old.request('GET',target+API);same(job,expected)
    if job['properties']['provisioningState']!='Succeeded':raise ValueError('Canary not ready')
    if stage=='start':
        if (folder/'dispatch.json').exists():raise ValueError('Preserve earlier canary execution')
        if listing(target+'/executions','2026-07-01'):raise ValueError('Reconcile unexpected prior execution')
        save(folder,'start-intent',{'resource':target,'template':expected['properties']['template']})
        ack=dns.old.request('POST',target+'/start'+API,data=expected['properties']['template'])
        save(folder,'dispatch',{'execution':ack['name']});print(json.dumps({'group':group,'execution':ack['name']}));return
    execution=protected(folder/'dispatch.json')['execution']
    run=dns.old.request('GET',target+'/executions/'+execution+API)
    same(run['properties']['template'],expected['properties']['template'])
    status=run['properties']['status']
    if status not in ('Succeeded','Failed'):print(json.dumps({'group':group,'status':status}));return
    if status=='Failed':raise ValueError('Canary failed; preserve evidence and restore DNS before proceeding')
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+PREFIX+"' | project Result=substring(Log_s,"+str(len(PREFIX))+')'
    rows=dns.old.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    if len(rows)!=1:print(json.dumps({'group':group,'status':status,'evidence':'pending'}));return
    proof=json.loads(rows[0]['Result'])
    if proof!={'passed':True,'group':group,'resolved_reference_count':len(expected['properties']['configuration']['secrets']),
              'peers':[{'host':h,'private_ip':ip} for h,ip in dns.EXPECTED.items()],
              'secret_values_logged':False,'normal_application_started':False,'database_writes':0,'broker_calls':0}:
        raise ValueError('Canary proof differs')
    save(folder,'result',{'passed':True,'proof':proof,'execution':execution,'fresh_resource_reference_resolution':True,
        'secret_version_rotation_tested':False,'cold_registry_pull_verified':False,
        'all_caller_application_acceptance':False,'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED'})
    print(json.dumps({'group':group,'passed':True,'references':proof['resolved_reference_count']}))

if __name__=='__main__':main(*sys.argv[1:])
