"""Atomic reference-only repair with full-row guards; never rewrite report hashes."""
import os,sys,json,hashlib
from pathlib import PurePosixPath
sys.path[:0]=['/app/src','/app/scripts']

def validate_package(package,tenant):
    items=package['qualified']
    if not package['passed'] or not package['clone_update_and_rollback_passed'] or len(items)!=375 or len({r['old_row']['generation_run_id'] for r in items})!=375:raise ValueError('Exact qualified scope required')
    for item in items:
        old=item['old_row'];after=item['after_row']
        if old['tenant_id']!=tenant or old['status']!='awaiting_approval':raise ValueError('Foreign or released row')
        if {k:v for k,v in after.items() if k!='output_reference'}!={k:v for k,v in old.items() if k!='output_reference'}:raise ValueError('Only the reference may change')
        path=PurePosixPath(old['output_reference']);target=PurePosixPath(after['output_reference'])
        if len(path.parts)!=5 or path.parts[:2]!=('internal-reports','review') or path.parts[2] not in ('U16088571','U16088606','hong-kong-china','alpha-world-ms'):raise ValueError('Unexpected source path')
        expected=path.with_name(path.stem+'__recovered_'+old['output_sha256']+path.suffix)
        if target!=expected or '..' in target.parts:raise ValueError('Recovery target differs')
        blob='/'.join(target.parts[1:])
        if package['copied_blobs'][blob]['sha256']!=old['output_sha256']:raise ValueError('Recovered bytes not qualified')
    return items

def main():
    import psycopg
    from psycopg.rows import dict_row
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient
    from ibkr_etl import web
    from ibkr_etl.util import canonical_json
    def sha(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()
    container=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential(exclude_interactive_browser_credential=True)).get_container_client('internal-reports')
    content=container.get_blob_client(PLAN['private_blob']).download_blob().readall()
    if len(content)>8*1024*1024 or hashlib.sha256(content).hexdigest()!=PLAN['sha256']:raise ValueError('Qualified repair package differs')
    package=json.loads(content);items=validate_package(package,web.TENANT_ID)
    for name,record in package['copied_blobs'].items():
        blob=container.get_blob_client(name,version_id=record['version_id'])
        if hashlib.sha256(blob.download_blob().readall()).hexdigest()!=record['sha256']:raise ValueError('Recovered version changed')
        if hashlib.sha256(container.get_blob_client(name).download_blob().readall()).hexdigest()!=record['sha256']:raise ValueError('Current recovered object differs')
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,options='-c statement_timeout=120000 -c lock_timeout=5000') as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        db.execute("SELECT set_config('app.tenant_id',%s,true)",(web.TENANT_ID,))
        if not db.execute("SELECT pg_try_advisory_xact_lock(hashtext('upgrade-report-reference-recovery-20261009')) AS acquired").fetchone()['acquired']:raise ValueError('Repair already running')
        before={}
        for table in package['report_fingerprints']:
            rows=[r['record'] for r in db.execute(f'SELECT to_jsonb(r) AS record FROM {table} r WHERE tenant_id=%s',(web.TENANT_ID,)).fetchall()]
            before[table]=rows
            if {'rows':len(rows),'sha256':sha(sorted(rows,key=canonical_json))}!=package['report_fingerprints'][table]:raise ValueError('Report or approval state drift')
        for item in items:
            old=item['old_row'];current=db.execute('SELECT to_jsonb(r) AS record FROM report_generation_runs r WHERE tenant_id=%s AND generation_run_id=%s FOR UPDATE',(web.TENANT_ID,old['generation_run_id'])).fetchone()['record']
            if current!=old:raise ValueError('Full row preimage drift')
            if db.execute('SELECT 1 FROM report_artifact_releases WHERE tenant_id=%s AND generation_run_id=%s LIMIT 1',(web.TENANT_ID,old['generation_run_id'])).fetchone():raise ValueError('Released artifact now involved')
        for item in items:
            old=item['old_row'];expected=item['after_row']
            count=db.execute('UPDATE report_generation_runs SET output_reference=%s WHERE tenant_id=%s AND generation_run_id=%s AND output_reference=%s AND output_sha256=%s AND status=%s',(expected['output_reference'],web.TENANT_ID,old['generation_run_id'],old['output_reference'],old['output_sha256'],'awaiting_approval')).rowcount
            actual=db.execute('SELECT to_jsonb(r) AS record FROM report_generation_runs r WHERE tenant_id=%s AND generation_run_id=%s',(web.TENANT_ID,old['generation_run_id'])).fetchone()['record']
            if count!=1 or actual!=expected:raise ValueError('Reference-only readback failed')
        replacements={r['old_row']['generation_run_id']:r['after_row'] for r in items};after={}
        for table,originals in before.items():
            expected=[replacements.get(r['generation_run_id'],r) for r in originals] if table=='report_generation_runs' else originals
            actual=[r['record'] for r in db.execute(f'SELECT to_jsonb(r) AS record FROM {table} r WHERE tenant_id=%s',(web.TENANT_ID,)).fetchall()]
            if sha(sorted(actual,key=canonical_json))!=sha(sorted(expected,key=canonical_json)):raise ValueError('Unrelated report state changed')
            after[table]={'rows':len(actual),'sha256':sha(sorted(actual,key=canonical_json))}
    print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':True,'reference_rows_updated':375,'output_hashes_changed':False,'statuses_changed':False,'released_files_changed':False,'approvals_changed':False,'report_fingerprints_after':after}),flush=True)
if __name__=='__main__':main()
