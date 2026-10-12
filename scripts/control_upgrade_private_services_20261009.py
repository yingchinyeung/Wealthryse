"""Two bounded existing-identity private endpoint acceptance executions."""
import hashlib,json,sys
from pathlib import Path
from urllib.parse import urlsplit
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant
from verify_installed_ms_worker_release import corrected_template
import deploy_t_runtime_release_20261009 as runtime
from control_upgrade_legacy_recovery_probe_20261009 import protected
from run_specialist_gateway_read_verification import retain
from audit_azure_runtime import az
runtime.request=request
ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage10'
API='?api-version=2024-03-01'

def save(folder,name,record):
    with (folder/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**record,'protected_receipt':retain(record,'upgrade-private-services-'+folder.name+'-'+name)},f,indent=2)

def main(stage,scope):
    if scope not in ('main','legacy'):raise ValueError('Unknown identity scope')
    name='ibkrv2stg-onboard-render' if scope=='main' else 'ibkr-v11-daily'
    path=rid('Microsoft.App/jobs',name);folder=ROOT/('private-services-'+scope+'-v2');job=request('GET',path+API)
    if stage=='start':
        if folder.exists():raise ValueError('Reconcile existing execution')
        runtime.idle(name)
        dns=protected(ROOT/'unlinked-dns-v2/verified.json')
        if not dns['passed'] or dns['dns_cutover']:raise ValueError('Unlinked DNS preparation required')
        image_proof=protected(ROOT/'legacy-private-build-proof-v1/result.json')
        image=image_proof['base_image'] if scope=='main' else image_proof['pushed_image']
        if not image_proof['passed']:raise ValueError('Qualified image required')
        container=job['properties']['template']['containers'][0]
        alias=next(e['secretRef'] for e in container['env'] if e['name']=='DATABASE_URL')
        secret=next(s for s in job['properties']['configuration']['secrets'] if s['name']==alias)
        expected_host='ibkrv2stg-auhecwc4itaoo.vault.azure.net' if scope=='main' else 'ibkrv11-5rcc62xxqblay.vault.azure.net'
        secret_url=secret['keyVaultUrl']
        if urlsplit(secret_url).hostname!=expected_host or urlsplit(secret_url).query:raise ValueError('Database-secret reference differs')
        targets=[{'kind':'secret','url':secret_url+'?api-version=7.4','private_ip':'10.50.10.8' if scope=='main' else '10.40.10.6','alias':alias}]
        tenant=next(e['value'] for e in container['env'] if e['name']=='PORTFOLIO_TENANT_ID')
        if tenant!='00000000-0000-0000-0000-000000000001':raise ValueError('Exact tenant required')
        legacy_url='https://ibkrv115rcc62xxqblay.blob.core.windows.net/analytics-reports/'+tenant+'/reference/reference_data.json'
        if scope=='main':
            preflight=protected(ROOT.parent/'platform-upgrade-20261009-stage9/live-cycle-preflight-v4/result.json')
            known=json.loads(preflight['records'][0]['Result'])['private_blob']
            if not known.startswith('review/platform-upgrade-20261009/live-cycle-preflight-') or '..' in known:raise ValueError('Known immutable blob reference required')
            targets.extend([{'kind':'blob','url':'https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net/internal-reports/'+known,'private_ip':'10.50.10.6','alias':'current-report-audit-blob'}, {'kind':'blob','url':legacy_url,'private_ip':'10.50.10.9','alias':'legacy-reference-blob'}])
        else:targets.append({'kind':'blob','url':legacy_url,'private_ip':'10.40.10.7','alias':'legacy-reference-blob'})
        source='TARGETS='+repr(targets)+'\n'+Path(__file__).with_name('probe_upgrade_private_services_20261009.py').read_text(encoding='utf-8')
        template=corrected_template(job,source);c=template['containers'][0];c['image']=image;c['env']=[e for e in c.get('env',[]) if e['name']=='AZURE_CLIENT_ID']
        if len(c['env'])!=1 or not c['env'][0].get('value'):raise ValueError('Exact existing managed identity required')
        env=request('GET',job['properties']['environmentId']+API)
        folder.mkdir();save(folder,'intent',{'template':template,'job_invariant':settings_invariant(job),'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'targets':targets,'log_workspace_id':env['properties']['appLogsConfiguration']['logAnalyticsConfiguration']['customerId'],'scope':'Diagnostic execution only; no saved-job, role, secret, source or DNS changes'})
        ack=request('POST',path+'/start'+API,data=template);save(folder,'dispatch',{'execution':ack['name']});print(json.dumps({'scope':scope,'execution':ack['name']}));return
    if stage!='read':raise ValueError('Unknown stage')
    intent=protected(folder/'intent.json');execution=protected(folder/'dispatch.json')['execution']
    if settings_invariant(job)!=intent['job_invariant']:raise ValueError('Saved job changed')
    run=request('GET',path+'/executions/'+execution+API);state=run['properties']['status'];print(json.dumps({'execution':execution,'state':state}))
    if state not in ('Succeeded','Failed'):return
    actual=run['properties']['template']['containers'][0];expected=intent['template']['containers'][0]
    if any(actual.get(k)!=expected.get(k) for k in ('image','command','args','env')):raise ValueError('Executed diagnostic differs')
    prefix='PRIVATE_SERVICE_PROOF '
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+"))"
    rows=az('monitor','log-analytics','query','-w',intent['log_workspace_id'],'--analytics-query',query)
    if len(rows)!=1:print('Final service proof not yet available');return
    result=json.loads(rows[0]['Result']);record={'passed':state=='Succeeded' and result['passed'],'execution':execution,'proof':result,'job_settings_preserved':True}
    if not (folder/'result.json').exists():save(folder,'result',record)
    print(json.dumps(record))

if __name__=='__main__':main(*sys.argv[1:])
