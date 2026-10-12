"""Read-only QA input lineage; retain bounded diagnostics, no provider fetches."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_g04_preflight_20261010 as b
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=b.ROOT;OUT=ROOT/'.work/platform-upgrade-20261011-stage32/input-lineage-v1'

def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (Path(__file__).name,'probe_upgrade_input_lineage_20261011.py','prepare_upgrade_fresh_reporting_remote_20261011.py')}

def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown operation')
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json');job=b.request('GET',b.PATH+b.API)
    if b.bindings([job])[b.JOB]!=baseline['bindings'][b.JOB]:raise ValueError('Bootstrap drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve previous trial')
        b.runtime.safe_window();b.runtime.idle(b.JOB)
        code='import types,sys\nq=types.ModuleType("qa_read_only");sys.modules[q.__name__]=q\nexec('+repr((ROOT/'scripts/prepare_upgrade_fresh_reporting_remote_20261011.py').read_text(encoding='utf-8'))+',q.__dict__)\n'
        code+=(ROOT/'scripts/probe_upgrade_input_lineage_20261011.py').read_text(encoding='utf-8')
        template=b.corrected_template(job,code);template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name']=='DATABASE_ADMIN_URL']
        if len(template['containers'][0]['env'])!=1 or not template['containers'][0]['env'][0].get('secretRef'):raise ValueError('Existing admin reference only')
        save(OUT,'intent',{'template':template,'sources':hashes(),'binding':b.bindings([job]),'scope':'SELECT-only reconciliation of QA snapshot differences against stored records and original Flex row hashes; no production or external-provider connection, no writes'})
        ack=b.request('POST',b.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution mismatch')
    execution_env(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='UPGRADE_INPUT_LINEAGE ' if state=='Succeeded' else 'UPGRADE_INPUT_LINEAGE_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting lineage proof');return
    proof=json.loads(rows[0]['Result'])
    if state=='Failed':
        if not (OUT/'failure.json').exists():save(OUT,'failure',{'execution':name,'proof':proof})
        print(json.dumps({'failed':True,'proof':proof}));return
    if proof.get('passed') is not True or proof.get('database_writes')!=0 or proof.get('production_connection_used') is not False:raise ValueError('Read-only proof failed')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':proof,'input_certification_complete':False})
    print(json.dumps(proof))

if __name__=='__main__':main(*sys.argv[1:])
