"""Private restricted-role recovery fingerprints and approved download checks."""
import asyncio,hashlib,json,os,time
from datetime import datetime,timezone
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient,ContentSettings
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from ibkr_etl import web
from run_onboarded_viewer_access import asgi_get

TABLES=('schema_migrations','nav_snapshots','position_snapshots','cash_transactions',
 'derived_portfolio_nav','derived_portfolio_cash_ledger','reporting_snapshots',
 'report_generation_runs','report_artifact_releases','report_release_decisions',
 'subject_report_template_assets','platform_subjects','app_users','app_user_roles',
 'user_account_access','subject_feature_states')

def main():
    started=time.perf_counter()
    result={'mode':RECOVERY_MODE,'database_writes':0,'gateway_tested':False,'real_sign_in_tested':False,'tables':{},'downloads':[],'owner_review_downloads':[],'captured_at':datetime.now(timezone.utc).isoformat()}
    with psycopg.connect(os.environ['DATABASE_URL'],options=os.environ['PGOPTIONS'],row_factory=dict_row) as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        connection.execute("SELECT set_config('app.tenant_id',%s,true)",(web.TENANT_ID,))
        if connection.execute("SELECT current_setting('transaction_read_only') AS value").fetchone()['value']!='on':raise ValueError('Read-only recovery required')
        columns=connection.execute("SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position").fetchall()
        result['schema_sha256']=hashlib.sha256(json.dumps(columns,default=str,sort_keys=True).encode()).hexdigest()
        for table in TABLES:
            digest=hashlib.sha256();count=0
            with connection.cursor(name='recovery_rows') as cursor:
                cursor.execute(sql.SQL('SELECT md5(row_to_json(t)::text) AS digest FROM {} t ORDER BY 1').format(sql.Identifier('public',table)))
                for row in cursor:digest.update((row['digest']+'\n').encode());count+=1
            result['tables'][table]={'rows':count,'sha256':digest.hexdigest()}
        viewers=connection.execute("SELECT DISTINCT u.entra_object_id,u.email FROM app_users u JOIN app_user_roles r ON r.tenant_id=u.tenant_id AND r.entra_object_id=u.entra_object_id WHERE u.tenant_id=%s AND u.status='active' AND r.role_name='viewer' AND r.role_status='active' AND r.effective_from<=now() AND (r.effective_to IS NULL OR r.effective_to>=now()) ORDER BY u.entra_object_id",(web.TENANT_ID,)).fetchall()
        if len(viewers)!=4:raise ValueError('Recovery viewer scope differs')
        owner=connection.execute("SELECT DISTINCT u.entra_object_id,u.email FROM app_users u JOIN app_user_roles r ON r.tenant_id=u.tenant_id AND r.entra_object_id=u.entra_object_id WHERE u.tenant_id=%s AND u.status='active' AND r.role_name='owner' AND r.role_status='active' AND r.effective_from<=now() AND (r.effective_to IS NULL OR r.effective_to>=now()) ORDER BY u.entra_object_id LIMIT 1",(web.TENANT_ID,)).fetchone()
        if owner is None:raise ValueError('No existing owner for isolated review access test')
        review_hashes={r['generation_run_id']:r['output_sha256'] for r in connection.execute("SELECT r.generation_run_id,r.output_sha256 FROM report_generation_runs r JOIN reporting_snapshots s ON s.snapshot_id=r.snapshot_id AND s.tenant_id=r.tenant_id WHERE r.tenant_id=%s AND s.subject_id='alpha-world-ms' AND r.status IN ('awaiting_approval','released_internal')",(web.TENANT_ID,)).fetchall()}
        artifacts=connection.execute("SELECT a.artifact_release_id,a.artifact_sha256,r.report_type,s.subject_id,s.period_end FROM report_artifact_releases a JOIN report_release_decisions d ON d.release_decision_id=a.release_decision_id AND d.tenant_id=a.tenant_id JOIN report_generation_runs r ON r.generation_run_id=a.generation_run_id AND r.tenant_id=a.tenant_id JOIN reporting_snapshots s ON s.snapshot_id=r.snapshot_id AND s.tenant_id=r.tenant_id WHERE a.tenant_id=%s AND a.release_scope='internal_shareholder' AND a.revoked_at IS NULL AND d.release_scope='internal_shareholder' AND d.status='approved' AND s.status IN ('frozen','approved') AND r.status='released_internal' ORDER BY s.subject_id,s.period_end,r.report_type",(web.TENANT_ID,)).fetchall()
    identity={k:str(v) for k,v in viewers[0].items()}
    for artifact in artifacts:
        sid=artifact['subject_id'];aid=artifact['artifact_release_id']
        status,body=asyncio.run(asgi_get(web.app,f'/api/accounts/{sid}/reporting/artifacts/{aid}/download',identity))
        digest=hashlib.sha256(body).hexdigest()
        if status!=200 or digest!=artifact['artifact_sha256']:raise ValueError('Approved report recovery failed')
        result['downloads'].append({'subject_id':sid,'period_end':str(artifact['period_end']),'report_type':artifact['report_type'],'artifact_release_id':aid,'sha256':digest,'bytes':len(body),'status':status})
    owner_identity={k:str(v) for k,v in owner.items()}
    status,body=asyncio.run(asgi_get(web.app,'/api/accounts/alpha-world-ms/reporting/monthly',owner_identity))
    if status!=200:raise ValueError('Owner review catalog recovery failed')
    catalog=json.loads(body)
    for month in catalog['months']:
        for artifact in month['review_artifacts']:
            run_id=artifact['generation_run_id']
            route=f'/api/accounts/alpha-world-ms/reporting/review-artifacts/{run_id}/download'
            status,content=asyncio.run(asgi_get(web.app,route,owner_identity))
            digest=hashlib.sha256(content).hexdigest()
            if status!=200 or digest!=review_hashes.get(run_id):raise ValueError('Owner review file recovery failed')
            result['owner_review_downloads'].append({'subject_id':'alpha-world-ms','period_end':month['period_end'],'report_type':artifact['report_type'],'generation_run_id':run_id,'sha256':digest,'bytes':len(content),'status':status,'scope':'owner-review'})
    if len(result['owner_review_downloads'])!=42:raise ValueError('Recovery must retain all 42 registered MS review files')
    status,_=asyncio.run(asgi_get(web.app,route,identity))
    if status!=403:raise ValueError('Viewer accessed owner-only review file')
    result['viewer_review_access_denied']=True
    status,_=asyncio.run(asgi_get(web.app,'/api/accounts/alpha-world-ms/reporting/monthly'))
    if status not in (401,403):raise ValueError('Anonymous report access was not denied')
    result.update(status='passed',anonymous_reports_denied=True,seconds=round(time.perf_counter()-started,3),limits=['Restricted-role application and download tests are not a real Entra browser sign-in or measured disaster RTO/RPO.'])
    data=json.dumps(result,sort_keys=True,default=str).encode();digest=hashlib.sha256(data).hexdigest()
    blob=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential()).get_blob_client('internal-reports','review/viewer-report-investigation-20261008/recovery-'+RECOVERY_MODE+'-reports-'+RECOVERY_ATTEMPT+'.json')
    blob.upload_blob(data,overwrite=False,metadata={'sha256':digest},content_settings=ContentSettings(content_type='application/json'))
    print(json.dumps({'status':'application_recovery_read_only_passed','mode':RECOVERY_MODE,'tables':len(TABLES),'approved_downloads':len(result['downloads']),'ms_owner_review_downloads':len(result['owner_review_downloads']),'database_writes':0}),flush=True)

if __name__=='__main__':main()
