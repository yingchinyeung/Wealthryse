"""Image-only sector-currency rollout; no report replacement or database writes."""
import copy,hashlib,json,os,re,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
import requests
import control_upgrade_g04_preflight_20261010 as b
import scan_ms_presentation_worker_candidate as scan
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
from rehearse_ms_worker_image_rollback import restore_protected
from deploy_upgrade_model_cash_20261010 import body
from deploy_ms_headers_v14r15 import clean,web_hash

ROOT=Path(__file__).resolve().parents[1]
STAGE=ROOT/'.work/platform-upgrade-20261011-stage35'
BEFORE=ROOT/'.work/platform-upgrade-20261011-stage34'
OUT=STAGE/'web-deployment-v1'
PATH=b.rid('Microsoft.App/containerApps','ibkrv2stg-web');API=b.API
REVISION='sector-fx-20261011-v1'
SUBJECTS={'U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'}

def accept(proof,image,hashes):
    if proof.get('passed') is not True or proof.get('image')!=image or proof.get('source_hashes')!=hashes:
        raise ValueError('Exact candidate source/runtime required')
    rows=proof.get('subjects',[])
    if len(rows)!=5 or {r['subject'] for r in rows}!=SUBJECTS or any(r['passed'] is not True for r in rows):raise ValueError('Five successful subjects required')
    if type(proof.get('database_writes')) is not int or proof['database_writes']!=0 or proof.get('production_connection_used') is not False or proof.get('source_overlays') is not False:raise ValueError('Private read-only boundary differs')
    if proof.get('broker_identity_evidence_verified') is not True:raise ValueError('Broker identity evidence required')
    aliases=proof.get('reconciled_aliases',[])
    if len(aliases)!=1 or (aliases[0]['conid'],aliases[0]['broker_master_symbol'],aliases[0]['frozen_report_symbol'],aliases[0]['predecessor_price_continuity_asserted'])!=('804144296','SKYD','PSKY',False):raise ValueError('Exact documented alias only')

def candidate(kind):
    folder=BEFORE/('consumer-agent-'+kind+'-v3')
    source,_,output=scan.verify(folder);image=output['registry']+'/'+output['repository']+'@'+output['digest']
    assessment=json.loads((folder/'scan-completed.json').read_bytes())
    archived,_=restore_protected(json.loads((folder/'scan-protected-receipt.json').read_bytes()))
    if archived!=assessment or assessment['image']!=image or assessment['assessment']['status']!='pass' or assessment['assessment']['blocking_findings']!=0:raise ValueError('Protected passing security assessment required')
    native=json.loads((folder/'native-build-qualified.json').read_bytes())
    if native.get('qualified') is not True or native['linux_tests']!=33 or native['proof']['candidate_hashes']!=source['candidate_hashes'] or not native['proof']['unrelated_files_preserved']:raise ValueError('Native source-preserving tests required')
    tested=protected(STAGE/('consumer-'+kind+'-runtime-v6/result.json'))
    accept(tested['proof'],image,source['candidate_hashes'])
    return image,source,tested

def unrelated(actual,expected):
    if set(actual)!=set(expected) or {k:v for k,v in actual.items() if k!='ibkrv2stg-web'}!={k:v for k,v in expected.items() if k!='ibkrv2stg-web'}:raise ValueError('Unrelated workload drift')

def sources():
    names=(Path(__file__).name,'test_reporting_sector_deploy_20261011.py','control_reporting_agent_runtime_v6_20261011.py','probe_reporting_agent_runtime_v6_20261011.py','reporting_symbol_reconciliation_20261011.py')
    return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in names}

def main(stage):
    if stage not in ('plan','apply','verify','rollback'):raise ValueError('Unknown deployment operation')
    live=b.request('GET',PATH+API);auth=b.request('GET',PATH+'/authConfigs/current'+API)
    if stage=='rollback':
        plan=protected(OUT/'plan.json')
        if web_hash(clean(live))!=plan['invariant'] or auth['properties']!=plan['auth']['properties'] or live['properties']['template']['containers'][0]['image']!=plan['image']:raise ValueError('Rollback boundary drift')
        unrelated(b.bindings(b.current_resources()),plan['bindings'])
        if (OUT/'rollback-intent.json').exists():raise ValueError('Reconcile prior rollback')
        save(OUT,'rollback-intent',{'at':datetime.now(timezone.utc).isoformat(),'image':plan['before_image']})
        ack=b.request('PATCH',PATH+API,data=plan['rollback_body']);save(OUT,'rollback-ack',{'state':ack['properties'].get('provisioningState')});return
    image,source,tested=candidate('web')
    baseline=protected(BEFORE/'resume-v1/result.json')['boundary']['workloads']
    prior=protected(ROOT/'.work/platform-upgrade-20261010-stage30/cash-summary-v1/runtime-v1/installed/result.json')
    before_image=source['base_image']
    if prior.get('passed') is not True or prior['image']!=before_image or prior['proof']['tests']!=42:raise ValueError('Previously qualified exact fallback required')
    if stage=='plan':
        if OUT.exists() or b.bindings(b.current_resources())!=baseline:raise ValueError('Existing plan or workload drift')
        if live['properties']['template']['containers'][0]['image']!=before_image or live['properties']['configuration']['activeRevisionsMode']!='Single':raise ValueError('Website baseline differs')
        if any(s.get('value') for s in live['properties']['configuration'].get('secrets',[])):raise ValueError('No inline secret capture')
        run=subprocess.run([sys.executable,'-m','unittest','scripts.test_reporting_sector_deploy_20261011','-q'],cwd=ROOT,env={**os.environ,'PYTHONPATH':'src'+os.pathsep+'scripts'},capture_output=True,text=True,timeout=60)
        if run.returncode or re.findall(r'Ran (\d+) tests',run.stderr)!=['12']:raise ValueError('Deployment safeguards failed')
        save(OUT,'plan',{'image':image,'before_image':before_image,'before':live,'auth':auth,'invariant':web_hash(clean(live)),'bindings':baseline,'sources':sources(),
            'apply_body':body(live,image,REVISION),'rollback_body':body(live,before_image,'sector-fx-rollback-20261011-v1'),
            'runtime_proof':tested['protected_receipt'],'fallback_proof':prior['protected_receipt'],'fallback_scope':'Previously qualified installed image; retains sector currency defect. No new live rollback interruption claimed.',
            'scope':'Only agent sector normalization into reporting currency. No broker data, NAV, reports, approvals, authentication, credentials, schedules or network change.',
            'guard_tests':12,'real_viewer_sign_in':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED'})
        print('Protected website deployment plan prepared');return
    plan=protected(OUT/'plan.json')
    if plan['image']!=image or sources()!=plan['sources'] or web_hash(clean(live))!=plan['invariant'] or auth['properties']!=plan['auth']['properties']:raise ValueError('Deployment source/settings/authentication drift')
    unrelated(b.bindings(b.current_resources()),plan['bindings'])
    if stage=='apply':
        b.runtime.safe_window()
        if (OUT/'apply-intent.json').exists() or live['properties']['template']['containers'][0]['image']!=before_image:raise ValueError('Reconcile earlier mutation')
        save(OUT,'apply-intent',{'at':datetime.now(timezone.utc).isoformat(),'plan_receipt':plan['protected_receipt']})
        latest=b.request('GET',PATH+API);latest_auth=b.request('GET',PATH+'/authConfigs/current'+API)
        if latest['properties']['template']['containers'][0]['image']!=before_image or web_hash(clean(latest))!=plan['invariant'] or latest_auth['properties']!=plan['auth']['properties']:raise ValueError('Immediate pre-patch drift')
        ack=b.request('PATCH',PATH+API,data=plan['apply_body']);save(OUT,'apply-ack',{'state':ack['properties'].get('provisioningState')});print('Website sector currency correction submitted');return
    p=live['properties'];revision='ibkrv2stg-web--'+REVISION
    if p['template']['containers'][0]['image']!=image or p['provisioningState']!='Succeeded' or p.get('latestReadyRevisionName')!=revision:print('Not ready; not verified');return
    running=b.request('GET',PATH+'/revisions/'+revision+API)['properties'];traffic=p['configuration']['ingress']['traffic']
    if running['healthState']!='Healthy' or not running['active'] or len(traffic)!=1 or traffic[0]['weight']!=100 or not (traffic[0].get('latestRevision') is True or traffic[0].get('revisionName')==revision):raise ValueError('Health or traffic failed')
    checks=[]
    for path in ('/','/api/portfolio/U16088571/holdings','/api/portfolio/alpha-world-t/cash'):
        response=requests.get('https://'+p['configuration']['ingress']['fqdn']+path,timeout=20,allow_redirects=False)
        if response.status_code!=401 and not(response.status_code==302 and '/.auth/login/' in urlsplit(response.headers.get('Location','')).path):raise ValueError('Anonymous access boundary failed')
        checks.append({'path':path,'status':response.status_code})
    if not (OUT/'verified.json').exists():save(OUT,'verified',{'passed':True,'image':image,'revision':revision,'healthy':True,'traffic_percent':100,'authentication_preserved':True,'unrelated_workloads_preserved':True,'nonimage_settings_preserved':True,'anonymous_checks':checks,'reports_and_approvals_changed':False,'broker_data_changed':False,'real_viewer_sign_in':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED','full_upgrade_complete':False})
    print(json.dumps({'passed':True,'revision':revision,'healthy':True,'full_upgrade_complete':False}))

if __name__=='__main__':main(*sys.argv[1:])
