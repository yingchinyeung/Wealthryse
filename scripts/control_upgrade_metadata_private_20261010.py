"""Bounded metadata-only probe using the existing dedicated identity/job."""
import json,sys
from pathlib import Path
import add_upgrade_metadata_private_path_20261010 as p
from verify_installed_ms_worker_release import corrected_template
import deploy_t_runtime_release_20261009 as runtime
runtime.request=p.old.request
OUT=p.OUT/'runtime-v1'


def accept(proof,ip,sha):
    if proof.get('passed') is not True or proof.get('vault')!='ibkrv11-5rcc62xxqblay' or proof.get('private_peer')!=ip or proof.get('source_sha256')!=sha:
        raise ValueError('Private source/target mismatch')
    if proof.get('verified_tls') is not True or not proof.get('statuses') or any(s!=200 for s in proof['statuses']):
        raise ValueError('TLS/request proof missing')
    if type(proof.get('listed')) is not int or not 0<=proof['listed']<=500:raise ValueError('Metadata count invalid')
    if any(type(proof.get(k)) is not int or proof[k]!=0 for k in ('secret_values_read','secret_values_changed')) or proof.get('permissions_changed') is not False or proof.get('production_dns_changed') is not False:
        raise ValueError('Metadata-only boundary violated')


def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    verified=p.protected(p.OUT/'verified.json')
    if verified.get('passed') is not True or verified['endpoint']!=p.ENDPOINT:raise ValueError('Endpoint not verified')
    identity=p.old.request('GET',p.monitor.IDENTITY+'?api-version=2023-01-31')
    job=p.old.request('GET',p.monitor.JOBPATH+p.monitor.API)
    p.monitor.verify_job(job,identity,scheduled=True)
    source=Path(__file__).with_name('secret_metadata_monitor_20261010.py')
    source_sha=p.sha(source)
    if stage=='start':
        if OUT.exists():raise ValueError('Reconcile previous private probe')
        runtime.safe_window();runtime.idle(p.monitor.JOB)
        p.monitor.verify_roles(identity)
        code='PRIVATE_IP='+repr(verified['private_ip'])+'\nSOURCE_SHA256='+repr(source_sha)+'\nMONITOR_SOURCE='+repr(source.read_text(encoding='utf-8'))+'\n'+Path(__file__).with_name('probe_upgrade_metadata_private_20261010.py').read_text(encoding='utf-8')
        template=corrected_template(job,code)
        c=template['containers'][0]
        if c.get('env')!=[{'name':'AZURE_CLIENT_ID','value':identity['properties']['clientId']}] or job['properties']['configuration'].get('secrets'):
            raise ValueError('No credential injection permitted')
        p.save(OUT,'intent',{'template':template,'saved_job_sha256':p.monitor.fingerprint(job),
            'source_sha256':source_sha,'private_ip':verified['private_ip'],'endpoint_receipt':verified['protected_receipt']})
        ack=p.old.request('POST',p.monitor.JOBPATH+'/start'+p.monitor.API,data=template)
        p.save(OUT,'dispatch',{'execution':ack['name']})
        print(json.dumps({'execution':ack['name'],'secret_values_requested':False}));return
    intent=p.protected(OUT/'intent.json')
    if p.monitor.fingerprint(job)!=intent['saved_job_sha256'] or source_sha!=intent['source_sha256']:
        raise ValueError('Saved job/source drift')
    name=p.protected(OUT/'dispatch.json')['execution']
    execution=p.old.request('GET',p.monitor.JOBPATH+'/executions/'+name+p.monitor.API)
    actual=execution['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args','env')):raise ValueError('Execution differs')
    status=execution['properties']['status']
    if status not in ('Succeeded','Failed'):print(status);return
    if status=='Failed':raise ValueError('Metadata-only private probe failed; preserve execution evidence')
    prefix='PRIVATE_METADATA_PROOF '
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))'
    rows=p.monitor.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    if len(rows)!=1:print('Awaiting private proof ingestion');return
    proof=json.loads(rows[0]['Result']);accept(proof,intent['private_ip'],source_sha)
    if not (OUT/'result.json').exists():p.save(OUT,'result',{'passed':True,'execution':name,'proof':proof,
        'saved_job_unchanged':True,'control_plane_secret_resolution_verified':False,'dns_cutover':False})
    print(json.dumps({'passed':True,'private_peer':intent['private_ip'],'secret_values_read':0}))


if __name__=='__main__':main(*sys.argv[1:])
