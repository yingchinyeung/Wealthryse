"""Read-only independent NAV reconciliation across all 105 active QA months."""
import hashlib,json,sys
from pathlib import Path
import control_monthly_snapshot_enrichment_20261011 as base
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=base.ROOT;b=base.b;OUT=base.OUT.parent/'all-monthly-nav-audit-v1'
FILES=[Path(__file__).name,'prepare_reporting_migration_clone_20261011.py','audit_monthly_nav_coverage_20261011.py']
def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in FILES}
PROBE='''
import hashlib,json,os,socket
import prepare_reporting_migration_clone_20261011 as q
from audit_monthly_nav_coverage_20261011 import audit
if os.getuid()!=10001 or q.TEST_DB!='upgrade_g04_migration_20261011' or {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Private QA boundary required')
issues=[];counts={};intervals=0
with q.connect(q.TEST_DB) as db:
    def state():return hashlib.sha256('\\n'.join(r['h'] for r in db.execute('SELECT md5(row_to_json(t)::text) AS h FROM reporting_snapshots t ORDER BY 1').fetchall()).encode()).hexdigest()
    if state()!=EXPECTED:raise ValueError('Exact migrated QA state required')
    rows=db.execute("SELECT subject_id,period_end,data_sha256,snapshot_payload_json FROM reporting_snapshots WHERE tenant_id=%s AND status IN ('frozen','approved') ORDER BY subject_id,period_end",(q.TENANT,)).fetchall()
    if len(rows)!=105:raise ValueError('Exactly 105 active months required')
    for row in rows:
        raw=row['snapshot_payload_json']
        if hashlib.sha256(raw.encode()).hexdigest()!=row['data_sha256']:raise ValueError('Frozen hash differs')
        p=json.loads(raw)
        if p['subject_id']!=row['subject_id'] or p['period_end']!=row['period_end']:raise ValueError('Frozen identity differs')
        try:
            result=audit(p);intervals+=result['nav_intervals'];counts[row['subject_id']]=counts.get(row['subject_id'],0)+1
        except ValueError as error:
            issue={'subject':row['subject_id'],'period_end':row['period_end'],'issue':str(error)};issues.append(issue)
            print('ALL_MONTHLY_NAV_ISSUE '+json.dumps(issue),flush=True)
    if state()!=EXPECTED:raise ValueError('QA changed during audit')
print('ALL_MONTHLY_NAV_FINAL '+json.dumps({'passed':not issues,'months_checked':105,'passed_by_subject':counts,'failed_months':len(issues),'nav_intervals_verified':intervals,'database_writes':0,'production_connection_used':False}),flush=True)
'''
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    baseline=protected(OUT.parent/'resume-v1/result.json');job=b.request('GET',b.PATH+b.API)
    if b.bindings([job])[b.JOB]!=baseline['boundary']['workloads'][b.JOB]:raise ValueError('Saved bootstrap drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve previous audit')
        migration=protected(base.OUT/'result.json')
        if not migration['passed']:raise ValueError('Qualified migration required')
        code='import types,sys\nsys.path[:0]=["/app/src","/app/scripts"]\n'
        for name in FILES[1:]:
            module=Path(name).stem;code+='m=types.ModuleType('+repr(module)+');sys.modules[m.__name__]=m\nexec('+repr((ROOT/'scripts'/name).read_text())+',m.__dict__)\n'
        code+='EXPECTED='+repr(migration['proof']['final_snapshot_fingerprint']['sha256'])+'\n'+PROBE
        template=b.corrected_template(job,code);template['containers'][0]['image']=migration['image']
        template['containers'][0]['env']=protected(base.OUT/'intent.json')['template']['containers'][0]['env']
        b.runtime.safe_window();b.runtime.idle(b.JOB)
        save(OUT,'intent',{'template':template,'sources':hashes(),'scope':'SELECT-only independent frozen-NAV versus monthly return/interval/contribution check across 105 active QA snapshots. No source overlays to application, no database writes, no production or report changes.'})
        ack=b.request('POST',b.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Audit source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state!='Succeeded':print(json.dumps({'state':state,'accepted':False}));return
    def read(prefix):
        rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
        return [json.loads(r['Result']) for r in rows]
    values=read('ALL_MONTHLY_NAV_FINAL ')
    if len(values)!=1:print('Awaiting audit receipt');return
    proof=values[0];issues=read('ALL_MONTHLY_NAV_ISSUE ')
    if proof['months_checked']!=105 or len(issues)!=proof['failed_months'] or sum(proof['passed_by_subject'].values())+len(issues)!=105 or proof['database_writes']!=0 or proof['production_connection_used'] is not False:raise ValueError('Incomplete audit')
    label='result' if proof['passed'] else 'findings'
    if not (OUT/(label+'.json')).exists():save(OUT,label,{'passed':proof['passed'],'execution':name,'proof':proof,'issues':issues,'deployed':False})
    print(json.dumps({'proof':proof,'issues':issues}))
if __name__=='__main__':main(*sys.argv[1:])
