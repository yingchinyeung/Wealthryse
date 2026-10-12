"""Correct only the obsolete empty-store precondition; preserve authority policy."""
import base64,copy,gzip,hashlib,json,sys
from pathlib import Path
import control_upgrade_readers_20261010 as readers
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant
from audit_azure_runtime import az
ROOT=readers.ROOT;OUT=ROOT/'.work/platform-upgrade-20261010-stage16/authority-check-v1'
JOB='ibkrv2stg-authority';PATH=rid('Microsoft.App/jobs',JOB);API=readers.API
OLD="        if dict(counts)!=dict(controls=0,grants=0): raise ValueError('Expected dormant tenant authority store')"
NEW="        if (set(counts)!={'controls','grants'} or any(type(counts[k]) is not int or counts[k]<0 for k in counts)\n            or counts['controls']>1 or (counts['grants']>0 and counts['controls']!=1)):\n            raise ValueError('Invalid tenant authority store shape')"

def trial_folder(mode):return OUT/('fallback-v2' if mode=='fallback' else mode)


def shape(p):
    q=p.get('tenant_authority_rows')
    if not isinstance(q,dict) or set(q)!={'controls','grants'} or any(type(q[k]) is not int or q[k]<0 for k in q) or q['controls']>1 or (q['grants']>0 and q['controls']!=1):raise ValueError('Invalid authority count shape')
    if p.get('status')!='passed' or p.get('database_access')!='verified' or not p.get('authority_source_checked') or not p.get('unregistered_principal_denied') or p.get('source_table_access') is not False or any(p.get(k)!=0 for k in ('authority_rows_written','agents_enabled','messages_sent')):raise ValueError('Authority preflight boundary differs')


def build_args():
    b=protected(readers.OUT/JOB/'baseline.json');old=readers.decode(b['job']['properties']['template']['containers'][0]['args'])
    before=(ROOT/'.work/platform-upgrade-20261010-stage16/original_authority_worker.py').read_text(encoding='utf-8')
    after=(ROOT/'scripts/run_specialist_authority_worker.py').read_text(encoding='utf-8')
    if before.count(OLD)!=1 or before.replace(OLD,NEW)!=after or not old.endswith(before):raise ValueError('Change exceeds obsolete count check')
    code=old[:-len(before)]+after
    compile(code,'authority-store-check','exec')
    encoded=base64.b64encode(gzip.compress(code.encode(),mtime=0)).decode()
    args=['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'specialist-authority-worker','exec'))"]
    return b,args,hashlib.sha256(code.encode()).hexdigest()


def main(stage,mode='candidate'):
    if mode not in ('candidate','fallback','installed'):raise ValueError('Unknown image mode')
    image=readers.candidate();b,args,digest=build_args();live=request('GET',PATH+API)
    target=readers.SOURCE if mode=='fallback' else image
    folder=trial_folder(mode)
    expected=copy.deepcopy(b['job'])
    if mode=='installed':
        expected['properties']['template']['containers'][0].update(image=image,args=args)
    if settings_invariant(live)!=settings_invariant(expected) or live['properties']['template']['containers'][0]['image']!=(image if mode=='installed' else readers.SOURCE):raise ValueError('Authority job drift')
    if stage=='freeze':
        for oldmode in ('candidate','fallback'):
            failed=protected(readers.OUT/(JOB+'-'+oldmode)/'failure.json')
            if failed['proof'].get('reason')!='Expected dormant tenant authority store':raise ValueError('Prior defect not proven')
        readers.save(OUT,'source',{'candidate_image':image,'baseline':b['protected_receipt'],'args':args,'payload_sha256':digest,
            'old_worker_sha256':hashlib.sha256((ROOT/'.work/platform-upgrade-20261010-stage16/original_authority_worker.py').read_bytes()).hexdigest(),
            'new_worker_sha256':hashlib.sha256((ROOT/'scripts/run_specialist_authority_worker.py').read_bytes()).hexdigest(),
            'scope':'Only preflight empty-store expectation replaced with bounded valid count shape; actual authority policy, permissions, enrolled agents and source data unchanged'})
        print('Protected scoped authority preflight correction');return
    source=protected(OUT/'source.json')
    if source['args']!=args or source['payload_sha256']!=digest or source['candidate_image']!=image:raise ValueError('Frozen correction drift')
    if stage=='start':
        if folder.exists():raise ValueError('Reconcile existing execution')
        if mode=='fallback' and not protected(OUT/'fallback-pre-dispatch-interruption.json')['compatible_fallback_execution_absent']:raise ValueError('Prior dispatch must be reconciled')
        readers.runtime.safe_window();readers.runtime.idle(JOB)
        template=copy.deepcopy(live['properties']['template'])
        for key in list(template):
            if template[key] is None:del template[key]
        template['containers'][0].update(image=target,args=args)
        for e in template['containers'][0].get('env',[]):
            if e.get('secretRef') and e.get('value')=='':del e['value']
        readers.save(folder,'intent',{'image':target,'template':template,'payload_sha256':digest,'saved_invariant':settings_invariant(live)})
        ack=request('POST',PATH+'/start'+API,data=template);readers.save(folder,'dispatch',{'execution':ack['name']})
        print(json.dumps({'mode':mode,'execution':ack['name']}));return
    if stage!='read':raise ValueError('Unknown stage')
    intent=protected(folder/'intent.json');execution=protected(folder/'dispatch.json')['execution']
    run=request('GET',PATH+'/executions/'+execution+API);actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Actual correction differs')
    mapping=readers.execution_env(JOB,actual.get('env',[]),wanted.get('env',[]))
    status=run['properties']['status'];print(json.dumps({'mode':mode,'status':status}))
    if status not in ('Succeeded','Failed'):return
    prefix='SPECIALIST_AUTHORITY_WORKER '
    q="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))'
    rows=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',q)
    if len(rows)!=1:print('Waiting for proof ingestion');return
    proof=json.loads(rows[0]['Result'])
    if status!='Succeeded':
        if not (folder/'failure.json').exists():readers.save(folder,'failure',{'passed':False,'execution':execution,'proof':proof})
        raise ValueError('Corrected authority preflight failed')
    shape(proof)
    if not (folder/'result.json').exists():readers.save(folder,'result',{'passed':True,'image':target,'execution':execution,'proof':proof,'payload_sha256':digest,'saved_settings_preserved':True,'credential_mapping':mapping})
    print(json.dumps({'mode':mode,'passed':True,'counts':proof['tenant_authority_rows']}))


if __name__=='__main__':main(*sys.argv[1:])
