"""Image-only handover deployment with exact schema, runtime and rollback gates."""
import hashlib,json,os,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
import requests
import control_upgrade_g04_preflight_20261010 as b
import release_report_handover_20261011 as release
import scan_ms_presentation_worker_candidate as scan
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
from rehearse_ms_worker_image_rollback import restore_protected
from deploy_upgrade_model_cash_20261010 import body
from deploy_ms_headers_v14r15 import clean,web_hash
from deploy_reporting_sector_web_20261011 import unrelated

ROOT=Path(__file__).resolve().parents[1];STAGE=ROOT/'.work/platform-upgrade-20261011-stage44'
OUT=STAGE/'web-deployment-v1';PATH=b.rid('Microsoft.App/containerApps','ibkrv2stg-web');API=b.API
REVISION='report-handover-20261011-v1'
SUBJECTS={'U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'}

def accept(candidate,fallback,image,hashes):
    for proof in (candidate,fallback):
        if proof.get('passed') is not True or type(proof.get('database_writes')) is not int or proof['database_writes']!=0:raise ValueError('Read-only native proof required')
        if proof.get('identity')!={'role':'ibkr_stg_web','ro':'on','ssl':True}:raise ValueError('Wrong role')
        rows=proof.get('subjects',[])
        if len(rows)!=5 or {r['subject'] for r in rows}!=SUBJECTS or not all(r['passed'] for r in rows):raise ValueError('Five successful catalogues required')
    if candidate['image']!=image or candidate['source_hashes']!=hashes or fallback['image']!=release.BASE:raise ValueError('Image/source mismatch')
    old={r['subject']:r for r in fallback['subjects']}
    for row in candidate['subjects']:
        if not set(old[row['subject']]['released_ids'])<=set(row['released_ids']):raise ValueError('Approved download lost')
        if row['months']!=old[row['subject']]['months']:raise ValueError('Monthly catalogue coverage changed')

def candidate():
    source,_,output=scan.verify(release.OUT);image=output['registry']+'/'+output['repository']+'@'+output['digest']
    record=json.loads((release.OUT/'scan-completed.json').read_bytes())
    archived,_=restore_protected(json.loads((release.OUT/'scan-protected-receipt.json').read_bytes()))
    if record!=archived or record['image']!=image or record['assessment']['status']!='pass' or record['assessment']['blocking_findings']!=0:raise ValueError('Exact protected security gate required')
    native=json.loads((release.OUT/'native-build-qualified.json').read_bytes())
    if not native['qualified'] or native['linux_tests']!=72 or native['proof']['candidate_hashes']!=source['candidate_hashes'] or not native['proof']['unrelated_files_preserved']:raise ValueError('Native qualification failed')
    migration=protected(STAGE/'migration-v2/schema-apply-v1/result.json')
    if not migration['passed'] or not migration['proof']['schema_committed']:raise ValueError('Verified migration required')
    qa=protected(ROOT/'.work/platform-upgrade-20261011-stage42/handover-qa-v1/result.json')
    if not qa['passed'] or qa['image']!=image or not qa['proof']['schema_rollback_passed'] or not qa['proof']['approval_rollback_passed']:raise ValueError('Rollback rehearsal required')
    installed=protected(STAGE/'catalogue-candidate-v1/result.json');fallback=protected(STAGE/'catalogue-fallback-v1/result.json')
    accept(installed['proof'],fallback['proof'],image,source['candidate_hashes'])
    return image,installed,fallback,migration

def sources():
    return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (Path(__file__).name,'test_handover_deployment_20261011.py','control_handover_catalogue_20261011.py','probe_handover_catalogue_20261011.py')}

def main(stage):
    if stage not in ('plan','apply','verify','rollback'):raise ValueError('Invalid operation')
    live=b.request('GET',PATH+API);auth=b.request('GET',PATH+'/authConfigs/current'+API)
    if stage=='rollback':
        plan=protected(OUT/'plan.json')
        if web_hash(clean(live))!=plan['invariant'] or auth['properties']!=plan['auth']['properties'] or live['properties']['template']['containers'][0]['image']!=plan['image']:raise ValueError('Rollback boundary drift')
        unrelated(b.bindings(b.current_resources()),plan['bindings'])
        if (OUT/'rollback-intent.json').exists():raise ValueError('Reconcile previous rollback')
        save(OUT,'rollback-intent',{'at':datetime.now(timezone.utc).isoformat(),'image':release.BASE,'schema_retained':True})
        ack=b.request('PATCH',PATH+API,data=plan['rollback_body']);save(OUT,'rollback-ack',{'state':ack['properties'].get('provisioningState')});return
    image,tested,fallback,migration=candidate()
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage43/closeout.json')['boundary']['workloads']
    if stage=='plan':
        release.unchanged()
        if OUT.exists() or b.bindings(b.current_resources())!=baseline:raise ValueError('Existing plan or boundary drift')
        if live['properties']['template']['containers'][0]['image']!=release.BASE or live['properties']['configuration']['activeRevisionsMode']!='Single':raise ValueError('Wrong baseline')
        if any(s.get('value') for s in live['properties']['configuration'].get('secrets',[])):raise ValueError('No inline secret capture')
        run=subprocess.run([sys.executable,'-m','unittest','scripts.test_handover_deployment_20261011','-q'],cwd=ROOT,env={**os.environ,'PYTHONPATH':'src'+os.pathsep+'scripts'},capture_output=True,text=True,timeout=60)
        if run.returncode or 'Ran 8 tests' not in run.stderr:raise ValueError('Deployment tests failed')
        save(OUT,'plan',{'image':image,'before_image':release.BASE,'before':live,'auth':auth,'invariant':web_hash(clean(live)),'bindings':baseline,'sources':sources(),
            'apply_body':body(live,image,REVISION),'rollback_body':body(live,release.BASE,'handover-rollback-20261011-v1'),
            'candidate_proof':tested['protected_receipt'],'fallback_proof':fallback['protected_receipt'],'schema_proof':migration['protected_receipt'],
            'scope':'Image-only website change preserving approved downloads while replacement review is pending. Future owner approval supersedes older same-month/type releases atomically. No report approvals, portfolio data, credentials, auth or network changes.',
            'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED'})
        print('Protected image-only plan prepared');return
    plan=protected(OUT/'plan.json')
    if sources()!=plan['sources'] or image!=plan['image'] or web_hash(clean(live))!=plan['invariant'] or auth['properties']!=plan['auth']['properties']:raise ValueError('Deployment drift')
    unrelated(b.bindings(b.current_resources()),plan['bindings'])
    if stage=='apply':
        b.runtime.safe_window()
        if (OUT/'apply-intent.json').exists() or live['properties']['template']['containers'][0]['image']!=release.BASE:raise ValueError('Reconcile earlier mutation')
        save(OUT,'apply-intent',{'at':datetime.now(timezone.utc).isoformat(),'plan_receipt':plan['protected_receipt']})
        latest=b.request('GET',PATH+API);latest_auth=b.request('GET',PATH+'/authConfigs/current'+API)
        if latest['properties']['template']['containers'][0]['image']!=release.BASE or web_hash(clean(latest))!=plan['invariant'] or latest_auth['properties']!=plan['auth']['properties']:raise ValueError('Immediate prepatch drift')
        ack=b.request('PATCH',PATH+API,data=plan['apply_body']);save(OUT,'apply-ack',{'state':ack['properties'].get('provisioningState')});print('Website handover update submitted');return
    p=live['properties'];revision='ibkrv2stg-web--'+REVISION
    if p['template']['containers'][0]['image']!=image or p['provisioningState']!='Succeeded' or p.get('latestReadyRevisionName')!=revision:print('Not ready; not verified');return
    running=b.request('GET',PATH+'/revisions/'+revision+API)['properties'];traffic=p['configuration']['ingress']['traffic']
    if running['healthState']!='Healthy' or not running['active'] or len(traffic)!=1 or traffic[0]['weight']!=100 or not(traffic[0].get('latestRevision') is True or traffic[0].get('revisionName')==revision):raise ValueError('Health or traffic failed')
    checks=[]
    for path in ('/','/api/portfolio/U16088571/holdings','/api/portfolio/alpha-world-t/cash'):
        response=requests.get('https://'+p['configuration']['ingress']['fqdn']+path,timeout=20,allow_redirects=False)
        if response.status_code!=401 and not(response.status_code==302 and '/.auth/login/' in urlsplit(response.headers.get('Location','')).path):raise ValueError('Anonymous access boundary failed')
        checks.append({'path':path,'status':response.status_code})
    if not (OUT/'verified.json').exists():save(OUT,'verified',{'passed':True,'image':image,'revision':revision,'healthy':True,'traffic_percent':100,'authentication_preserved':True,'unrelated_workloads_preserved':True,'nonimage_settings_preserved':True,'anonymous_checks':checks,'reports_and_approvals_changed':False,'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','full_upgrade_complete':False})
    print(json.dumps({'passed':True,'revision':revision,'full_upgrade_complete':False}))
if __name__=='__main__':main(sys.argv[1])
