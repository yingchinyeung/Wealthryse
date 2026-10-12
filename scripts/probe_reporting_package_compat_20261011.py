"""Read-only package preservation with exact installed candidate or fallback."""
import hashlib,json,os,socket,sys
from pathlib import Path
import qa_read_only as q
def main():
    if os.getuid()!=10001 or q.TEST_DB!='upgrade_g04_20261010':raise ValueError('Exact isolated QA required')
    if {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Wrong private endpoint')
    for name,h in SOURCE_HASHES.items():
        if hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()!=h:raise ValueError('Installed file differs: '+name)
    sys.path[:0]=['/app/src','/app/scripts','/app/worker']
    import generate_month_end_package as worker
    from ibkr_etl.presentation_design import presentation_content_contract_version
    with q.connect(q.TEST_DB) as raw:
        if raw.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Readonly required')
        class Adapter:
            def execute(self,sql,args=()):
                if not sql.lstrip().upper().startswith('SELECT '):raise ValueError('SELECT only')
                return raw.execute(sql.replace('?', '%s'),args)
        def state():return {t:hashlib.sha256('\n'.join(r['h'] for r in raw.execute('SELECT md5(row_to_json(t)::text) AS h FROM '+t+' t ORDER BY 1').fetchall()).encode()).hexdigest() for t in q.REPORT_TABLES}
        before=state();results=[]
        for subject in ('U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'):
            rows=raw.execute("SELECT r.template_version FROM report_generation_runs r JOIN reporting_snapshots s ON s.tenant_id=r.tenant_id AND s.snapshot_id=r.snapshot_id WHERE r.tenant_id=%s AND s.subject_id=%s AND s.period_end='2026-09-30' AND r.report_type='factsheet' ORDER BY r.generation_run_id DESC LIMIT 1",(q.TENANT,subject)).fetchall()
            if len(rows)!=1:raise ValueError('Existing factsheet unavailable')
            versions={'factsheet':rows[0]['template_version'],'presentation':presentation_content_contract_version(subject)}
            status=worker._existing_package(Adapter(),tenant_id=q.TENANT,account_id=subject,period_end='2026-09-30',expected_versions=versions)
            if status not in ('already_generated','already_released'):raise ValueError('Existing package requires controlled regeneration: '+subject)
            results.append({'subject':subject,'status':status,'presentation_contract':versions['presentation']})
        if state()!=before:raise ValueError('QA report state changed')
    print('REPORTING_COMPAT_FINAL '+json.dumps({'passed':True,'mode':MODE,'subjects':results,'installed_hashes':SOURCE_HASHES,'qa_report_state_unchanged':True,'database_writes':0,'blob_writes':0,'model_calls':0,'production_connection_used':False,'normal_entrypoints_run':False}),flush=True)
if __name__=='__main__':
    try:main()
    except Exception as e:
        print('REPORTING_COMPAT_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e)[:500],'reconcile_before_retry':True}),flush=True)
        raise SystemExit(1)
