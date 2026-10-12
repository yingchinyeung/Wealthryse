"""One-shot candidate/fallback ingestion qualification on legacy recovery only."""
import base64,hashlib,json,sys
from pathlib import Path
import scan_ms_presentation_worker_candidate as scan
from release_upgrade_legacy_security_20261010 import SOURCE,REGISTRY,API
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant
from verify_installed_ms_worker_release import corrected_template
from rehearse_ms_worker_image_rollback import restore_protected
from control_upgrade_legacy_recovery_probe_20261009 import protected
from run_specialist_gateway_read_verification import retain
from audit_azure_runtime import az
import deploy_t_runtime_release_20261009 as runtime
runtime.request=request
scan.scan.REGISTRY=REGISTRY+'.azurecr.io';scan.build.REGISTRY=REGISTRY
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261010-stage15/legacy-security-v2'
JOB='ibkr-v11-daily';PATH=rid('Microsoft.App/jobs',JOB)
PROBE=Path(__file__).with_name('probe_upgrade_legacy_ingestion_20261010.py')
CHECKS={'synthetic_flex_import_reconciles','unchanged_replay_idempotent','sqlite_csv_export_preserves_values','temporary_write_rollback'}


def candidate():
    _,_,output=scan.verify(OUT)
    image=output['registry']+'/'+output['repository']+'@'+output['digest']
    record=json.loads((OUT/'scan-completed.json').read_bytes())
    remote,_=restore_protected(json.loads((OUT/'scan-protected-receipt.json').read_bytes()))
    if record!=remote or record['image']!=image or record['assessment']['status']!='pass' or record['assessment']['blocking_findings']:
        raise ValueError('Exact protected passing security scan required')
    return image


def save(folder,name,value):
    with (folder/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-legacy-security-'+folder.name+'-'+name)},f,indent=2)


def accept(p):
    if not p.get('passed') or set(p.get('checks',[]))!=CHECKS or len(p.get('checks',[]))!=4:raise ValueError('All four ingestion checks required')
    if p.get('public_tables_checked')!=25 or p.get('temporary_relations')!=25 or p.get('public_table_differences')!=[]:raise ValueError('Clone preservation incomplete')
    if not p.get('database_read_only_enforced') or not p.get('temporary_objects_dropped_on_disconnect'):raise ValueError('Database guards missing')
    if any(p.get(k)!=0 for k in ('production_connections','production_writes','persistent_clone_writes','broker_calls','cloud_storage_writes')):raise ValueError('Forbidden external side effect')


def main(stage,mode,revision='4'):
    if mode not in ('candidate','fallback'):raise ValueError('Unknown mode')
    if revision not in ('1','2','3','4') or (stage=='start' and revision!='4'):raise ValueError('Only current rehearsal may start')
    image=candidate() if mode=='candidate' else SOURCE
    # Always require the candidate's independent security gate, including fallback tests.
    candidate()
    folder=OUT/('ingestion-'+mode+'-v'+revision)
    job=request('GET',PATH+API)
    original=json.loads((OUT/'source-intent.json').read_bytes())['before_jobs'][JOB]
    if job['properties']['template']['containers'][0]['image']!=SOURCE or settings_invariant(job)!=original['settings_invariant_sha256']:raise ValueError('Installed legacy drift')
    if stage=='start':
        if folder.exists():raise ValueError('Reconcile existing execution, never repeat')
        runtime.safe_window();runtime.idle(JOB)
        fixture=(OUT/'context/tests/fixtures/sample_flex.xml').read_bytes()
        code="import base64\nFIXTURE_CONTENT=base64.b64decode("+repr(base64.b64encode(fixture).decode())+")\n"+PROBE.read_text(encoding='utf-8')
        compile(code,'legacy-ingestion-fixture','exec')
        template=corrected_template(job,code);c=template['containers'][0];c['image']=image
        c['env']=[e for e in c.get('env',[]) if e['name'] in ('DATABASE_URL','PORTFOLIO_TENANT_ID')]
        if {e['name'] for e in c['env']}!={'DATABASE_URL','PORTFOLIO_TENANT_ID'} or not next(e for e in c['env'] if e['name']=='DATABASE_URL').get('secretRef'):raise ValueError('Only referenced database credentials allowed')
        folder.mkdir();save(folder,'intent',{'image':image,'template':template,'source_sha256':hashlib.sha256(code.encode()).hexdigest(),
            'job_invariant':settings_invariant(job),'scope':'Exact private legacy recovery clone; 25 synthetic temporary relations; persistent read-only; no Flex token, broker request or cloud upload'})
        ack=request('POST',PATH+'/start'+API,data=template);save(folder,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    if stage!='read':raise ValueError('Unknown stage')
    intent=protected(folder/'intent.json');execution=protected(folder/'dispatch.json')['execution']
    run=request('GET',PATH+'/executions/'+execution+API)
    actual=run['properties']['template']['containers'][0];expected=intent['template']['containers'][0]
    if any(actual.get(k)!=expected.get(k) for k in ('image','command','args')):raise ValueError('Actual probe differs')
    state=run['properties']['status'];print(json.dumps({'mode':mode,'status':state}))
    if state not in ('Succeeded','Failed'):return
    prefix='LEGACY_INGESTION_PROOF '
    q="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))'
    rows=az('monitor','log-analytics','query','-w','f594ac4f-31eb-4684-a5af-a8adc5020cfa','--analytics-query',q)
    if state!='Succeeded':
        diagprefix='LEGACY_PRESERVATION_DIAGNOSTIC '
        diagq="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+diagprefix+"' | project Result=parse_json(substring(Log_s,"+str(len(diagprefix))+'))'
        diagnostics=az('monitor','log-analytics','query','-w','f594ac4f-31eb-4684-a5af-a8adc5020cfa','--analytics-query',diagq)
        if not (folder/'failure.json').exists():save(folder,'failure',{'passed':False,'image':image,'execution':execution,'status':state,
            'hash_only_preservation_diagnostics':[json.loads(r['Result']) for r in diagnostics],
            'release_gate_passed':False,'saved_job_preserved':True})
        raise ValueError('Legacy rehearsal failed; protected failure retained')
    if len(rows)!=1:print('Final proof not yet ingested');return
    proof=json.loads(rows[0]['Result']);accept(proof)
    if not (folder/'result.json').exists():save(folder,'result',{'passed':True,'image':image,'execution':execution,'proof':proof,'saved_job_preserved':True})
    print(json.dumps({'mode':mode,'passed':True,'checks':4,'public_tables_unchanged':25}))


if __name__=='__main__':main(*sys.argv[1:])
