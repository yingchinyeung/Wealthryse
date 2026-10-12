"""Run actual registry/worker OS processes against read-only PostgreSQL.

Preceded by a same-image no-generation rehearsal; no model call is needed for
the five existing September packages. Fingerprint report and approval state.
"""
import hashlib,json,os,subprocess,sys
from pathlib import Path
os.environ.update(WORKER_SETTINGS)
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=30000'
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl.db import connect

def state():
    connection=connect(Path('/tmp/unused.sqlite3'),os.environ['DATABASE_URL'],
        os.environ['PORTFOLIO_TENANT_ID'])
    try:
        result={}
        for table,projection in (
            ('reporting_snapshots','snapshot_id,status,data_sha256'),
            ('report_generation_runs','*'),
            ('report_release_decisions','*'),
            ('report_artifact_releases','*')):
            rows=connection.execute(f'SELECT * FROM (SELECT {projection} FROM {table} WHERE tenant_id=?) t',
                (os.environ['PORTFOLIO_TENANT_ID'],)).fetchall()
            values=sorted(json.dumps(dict(row),sort_keys=True,default=str) for row in rows)
            result[table]={'rows':len(rows),'sha256':hashlib.sha256('\n'.join(values).encode()).hexdigest()}
        return result
    finally:connection.close()

def main():
    if hashlib.sha256(Path('/app/scripts/generate_month_end_package.py').read_bytes()).hexdigest()!=EXPECTED_WORKER_SHA:
        raise ValueError('Installed worker differs')
    before=state()
    run=subprocess.run([sys.executable,'/app/scripts/run_registry_month_end_packages.py'],
        cwd='/app',env=dict(os.environ),capture_output=True,text=True,timeout=900)
    after=state()
    if before!=after:raise ValueError('Report or approval state changed')
    if run.returncode:raise ValueError('Normal registry subprocess failed; inspect privately without repeating')
    lines=[json.loads(line) for line in run.stdout.splitlines() if line.strip().startswith('{')]
    packages=[r for r in lines if r.get('status') in ('already_generated','already_released')]
    summaries=[r for r in lines if r.get('status')=='success' and 'subjects' in r]
    expected={'alpha-world-ms','alpha-world-t','hong-kong-china','U16088571','U16088606'}
    if (len(packages)!=5 or len(summaries)!=1 or {r['subject_id'] for r in summaries[0]['subjects']}!=expected
        or any(r['status']!='processed' for r in summaries[0]['subjects'])
        or any(str(r['period_end'])!='2026-09-30' for r in packages)):
        raise ValueError('All five actual processes must reuse September packages')
    print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':True,'worker_sha256':EXPECTED_WORKER_SHA,
        'actual_registry_and_worker_subprocesses':True,'subjects':summaries[0]['subjects'],
        'packages_reused':len(packages),'database_mode':'read_only','report_and_approval_state':after,
        'report_and_approval_state_unchanged':True,'new_reports_generated':0}),flush=True)

if __name__=='__main__':main()
