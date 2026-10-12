"""Use the scanned immutable image against existing private QA only."""
import hashlib,json,sys
from pathlib import Path
import release_reporting_corrections_20261011 as release
import control_upgrade_corrected_reports_20261011 as base
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment
from rehearse_ms_worker_image_rollback import restore_protected
import scan_ms_presentation_worker_candidate as scan
a=base.a;b=base.b;ROOT=base.ROOT
OUT=ROOT/'.work/platform-upgrade-20261011-stage34/image-qa-v1'
FILES=['scripts/'+Path(__file__).name,'scripts/probe_upgrade_corrected_reports_20261011.py','scripts/prepare_upgrade_fresh_reporting_remote_20261011.py']
def hashes():return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in FILES}

def probe_source():
    source=(ROOT/FILES[1]).read_text(encoding='utf-8')
    replacements={
        "PREFIX='candidate-stage33-v1/'":"PREFIX='candidate-stage34-image-qa-v1/'",
        "if asset['container_name']!='internal-reports':raise ValueError('Unexpected template container')":"if service.get_container_client(asset['container_name']).get_container_properties().get('public_access') is not None:raise ValueError('Approved template container must be private')",
        "validation['status']":"validation.get('status', validation.get('ms_financial_audit', {}).get('status'))",
        "else root/'render_monthly_presentation.mjs'":"else Path('/app/scripts/render_monthly_presentation.mjs')",
        "    for name,source in PYTHON_CANDIDATES.items():\n        module=types.ModuleType(name);module.__package__='ibkr_etl';sys.modules[name]=module\n        exec(source,module.__dict__)":"    for name,digest in INSTALLED_HASHES.items():\n        if sha((Path('/app')/name).read_bytes())!=digest:raise ValueError('Installed source mismatch: '+name)",
        "        for name,source in JS_CANDIDATES.items():\n            if Path(name).name!=name or not name.endswith('.mjs'):raise ValueError('Bad source name')\n            (root/name).write_text(source,encoding='utf-8')":"        # Execute installed renderers without source overlays.",
        "            for key in original:":"            if subject in ('U16088571','U16088606','hong-kong-china'):\n                from ibkr_etl.reporting_snapshot import _collect_tool_results\n                from ibkr_etl.agent_tools import _sector_allocation\n                collected=_collect_tool_results(Adapter(),tenant_id=q.TENANT,subject_id=subject,period_end=p['period_end'],reporting_currency=p['reporting_currency'])\n                if collected['holdings']['data']!=p['holdings']:raise ValueError('Installed collector differs')\n                sector=_sector_allocation(Adapter(),subject,p['data_as_of'],{}).as_dict()['data']\n                for key in ('currency','valuation_method','gross_market_value','allocation'):\n                    if sector[key]!=p['sector_allocation'][key] or collected['sector_allocation']['data'][key]!=sector[key]:raise ValueError('Installed sector integration differs')\n            for key in original:",
    }
    for old,new in replacements.items():
        if source.count(old)!=1:raise ValueError('QA source anchor drift: '+old[:80])
        source=source.replace(old,new)
    if 'PYTHON_CANDIDATES' in source or 'JS_CANDIDATES' in source:raise ValueError('Source overlay remains')
    return source

def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    source,native,image=scan.verify(release.OUT)
    scanned=release.r.read(release.OUT/'scan-completed.json')
    secured,_=restore_protected(release.r.read(release.OUT/'scan-protected-receipt.json'))
    if secured!=scanned:raise ValueError('Protected scan differs')
    image_ref=image['registry']+'/'+image['repository']+'@'+image['digest']
    if scanned['image']!=image_ref or scanned['assessment']['status']!='pass':raise ValueError('Exact clean image required')
    baseline=protected(OUT.parent/'resume-v1/result.json');job=b.request('GET',a.PATH+b.API)
    if b.bindings([job])[a.JOB]!=baseline['boundary']['workloads'][a.JOB]:raise ValueError('Saved worker drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve earlier execution; reconcile')
        snapshots=protected(ROOT/'.work/platform-upgrade-20261011-stage31/fresh-snapshots-v1/result.json')
        prior=protected(OUT.parent/'t-integration-v1/intent.json')
        code='import types,sys,os\nos.environ["DATABASE_ADMIN_URL"]=os.environ["DATABASE_URL"]\nq=types.ModuleType("qa_read_only");sys.modules[q.__name__]=q\nexec('+repr((ROOT/FILES[2]).read_text(encoding='utf-8'))+',q.__dict__)\n'
        code+='EXPECTED_HASHES='+repr({s['subject']:s['sha256'] for s in snapshots['proof']['subjects']})+'\nINSTALLED_HASHES='+repr(source['candidate_hashes'])+'\n'+probe_source()
        template=b.corrected_template(job,code);template['containers'][0]['image']=image_ref
        template['containers'][0]['env']=prior['template']['containers'][0]['env']
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        if b.bindings(b.current_resources())!=baseline['boundary']['workloads']:raise ValueError('Workload drift')
        save(OUT,'intent',{'template':template,'sources':hashes(),'image':image_ref,'candidate_hashes':source['candidate_hashes'],
            'scope':'Scanned image, installed source only; ten private QA outputs, read-only recovery DB. No saved job update, production DB connection, report registration or approvals.'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name'],'deployed':False}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Probe changed')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    exact_environment(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='UPGRADE_CORRECTED_FINAL ' if state=='Succeeded' else 'UPGRADE_CORRECTED_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting receipt ingestion');return
    proof=json.loads(rows[0]['Result'])
    if state=='Succeeded' and (not proof.get('passed') or proof.get('artifacts')!=10 or not proof.get('qa_state_unchanged') or proof.get('database_writes')!=0 or proof.get('registered_reports')!=0 or proof.get('production_connection_used') is not False or any(s['validation_status']!='passed' for s in proof['subjects'])):raise ValueError('Incomplete image QA proof')
    label='result' if state=='Succeeded' else 'failure'
    if not (OUT/(label+'.json')).exists():save(OUT,label,{'passed':state=='Succeeded','execution':name,'proof':proof,'image':image_ref,'deployed':False})
    print(json.dumps({'state':state,'proof':proof}))
if __name__=='__main__':main(*sys.argv[1:])
