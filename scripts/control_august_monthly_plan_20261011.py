"""Read-only, hash-bound plan for the one independently identified stale month."""
import hashlib,json,sys
from pathlib import Path
import control_monthly_snapshot_enrichment_20261011 as base
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=base.ROOT;b=base.b;OUT=base.OUT.parent/'august-monthly-plan-v1'
MODULES=['prepare_reporting_migration_clone_20261011','audit_monthly_nav_coverage_20261011','audit_monthly_nav_coverage_v2_20261011',
         'reconcile_legacy_monthly_reporting_20261011','test_legacy_monthly_reporting_20261011','reconcile_august_monthly_reporting_20261011','test_august_monthly_reporting_20261011']
FILES=[Path(__file__).name,*[n+'.py' for n in MODULES]]
def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in FILES}
PROBE='''
import hashlib,json,os,socket,sys,unittest
import prepare_reporting_migration_clone_20261011 as q
from reconcile_august_monthly_reporting_20261011 import correct,digest
from ibkr_etl.monthly_reporting_analytics import build_monthly_reporting_analytics
if os.getuid()!=10001 or q.TEST_DB!='upgrade_g04_migration_20261011' or {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Private QA boundary required')
result=unittest.TextTestRunner(verbosity=0).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules['test_august_monthly_reporting_20261011']))
if not result.wasSuccessful() or result.testsRun!=10:raise ValueError('Native repair tests failed')
with q.connect(q.TEST_DB) as db:
    def state():return hashlib.sha256('\\n'.join(r['h'] for r in db.execute('SELECT md5(row_to_json(t)::text) AS h FROM reporting_snapshots t ORDER BY 1').fetchall()).encode()).hexdigest()
    if state()!=EXPECTED:raise ValueError('Exact QA snapshot state required')
    rows=db.execute("SELECT snapshot_id,subject_id,period_end,data_sha256,snapshot_payload_json,status FROM reporting_snapshots WHERE tenant_id=%s AND subject_id='U16088606' AND period_end='2026-08-31' AND status IN ('frozen','approved')",(q.TENANT,)).fetchall()
    if len(rows)!=1:raise ValueError('One active August snapshot required')
    row=rows[0];p=json.loads(row['snapshot_payload_json'])
    if hashlib.sha256(row['snapshot_payload_json'].encode()).hexdigest()!=row['data_sha256']:raise ValueError('Frozen integrity differs')
    class Adapter:
        def execute(self,query,args=()):
            if not query.lstrip().upper().startswith('SELECT '):raise ValueError('SELECT only')
            return db.execute(query.replace('?','%s'),args)
    def calculate():return build_monthly_reporting_analytics(Adapter(),subject_id='U16088606',period_end='2026-08-31',reporting_currency='USD')
    fresh=calculate();candidate=correct(p,fresh)
    if correct(p,calculate())!=candidate:raise ValueError('Unchanged replay differs')
    if state()!=EXPECTED:raise ValueError('QA changed')
print('AUGUST_MONTHLY_PLAN '+json.dumps({'passed':True,'native_tests':10,'subject':'U16088606','period_end':'2026-08-31','status':row['status'],'source_snapshot_id':str(row['snapshot_id']),
    'source_sha256':row['data_sha256'],'candidate_sha256':digest(candidate),'calculation_sha256':digest(fresh),'old_intervals':p['monthly_reporting']['daily_observations'],'new_intervals':fresh['data']['daily_observations'],
    'monthly_return_nav_cash_unchanged':True,'candidate_verified':True,'replay_passed':True,'database_writes':0,'production_connection_used':False}),flush=True)
'''
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown operation')
    baseline=protected(OUT.parent/'resume-v1/result.json');job=b.request('GET',b.PATH+b.API)
    if b.bindings([job])[b.JOB]!=baseline['boundary']['workloads'][b.JOB]:raise ValueError('Saved bootstrap drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve earlier plan')
        diagnosis=protected(OUT.parent/'all-monthly-nav-audit-v2/findings.json');migration=protected(base.OUT/'result.json')
        if len(diagnosis['issues'])!=1 or diagnosis['issues'][0]['subject']!='U16088606' or diagnosis['issues'][0]['period_end']!='2026-08-31' or not migration['passed']:raise ValueError('Exact diagnosed scope required')
        code='import types,sys\nsys.path[:0]=["/app/src","/app/scripts"]\n'
        for name in MODULES:code+='m=types.ModuleType('+repr(name)+');sys.modules[m.__name__]=m\nexec('+repr((ROOT/'scripts'/(name+'.py')).read_text())+',m.__dict__)\n'
        code+='EXPECTED='+repr(migration['proof']['final_snapshot_fingerprint']['sha256'])+'\n'+PROBE
        template=b.corrected_template(job,code);template['containers'][0]['image']=migration['image'];template['containers'][0]['env']=protected(base.OUT/'intent.json')['template']['containers'][0]['env']
        b.runtime.safe_window();b.runtime.idle(b.JOB)
        save(OUT,'intent',{'template':template,'sources':hashes(),'scope':'Read-only private August attribution correction plan. No frozen data writes, production connection, reports or approval changes.'})
        ack=b.request('POST',b.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Frozen source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state!='Succeeded':print(json.dumps({'state':state,'accepted':False}));return
    prefix='AUGUST_MONTHLY_PLAN '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
    if len(rows)!=1:print('Awaiting receipt');return
    proof=json.loads(rows[0]['Result'])
    if not proof['passed'] or proof['native_tests']!=10 or proof['old_intervals']!=19 or proof['new_intervals']!=21 or not proof['monthly_return_nav_cash_unchanged'] or not proof['replay_passed'] or proof['database_writes']!=0 or proof['production_connection_used'] is not False:raise ValueError('Incomplete repair plan')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':proof,'deployed':False})
    print(json.dumps(proof))
if __name__=='__main__':main(*sys.argv[1:])
