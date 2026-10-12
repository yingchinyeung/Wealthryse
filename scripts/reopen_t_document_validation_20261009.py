"""Owner-authorized, T-only recovery of a failed, unreleased document binding.

One transaction; rehearses and rolls back the exact updates before committing.
Never edits financial data, approvals, documents, failed batches, or access.
"""
import hashlib,json,os
from datetime import datetime,timezone
import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient,ContentSettings

TENANT='00000000-0000-0000-0000-000000000001'
CASE='e71119a4-882f-4f87-87ed-024f7ea9fe73'
BATCH='4bc5005a-039a-4518-85cc-adac1cf1e99a'
EVENT='alpha_world_t_document_stage_reopened_20261009'

def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()
def digest(value): return hashlib.sha256(canonical(value)).hexdigest()

def main():
    if os.environ.get('PORTFOLIO_TENANT_ID')!=TENANT: raise ValueError('Wrong tenant')
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    def retain(label,value):
        data=canonical(value); sha=hashlib.sha256(data).hexdigest()
        blob='review/alpha-world-t/document-recovery-20261009/'+label+'-'+sha+'.json'
        client=service.get_blob_client('internal-reports',blob)
        client.upload_blob(data,overwrite=False,content_settings=ContentSettings(content_type='application/json'))
        if client.download_blob().readall()!=data: raise ValueError('Private evidence readback mismatch')
        return {'blob':blob,'sha256':sha}
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=25,
            options='-c statement_timeout=60000 -c lock_timeout=10000') as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
        c.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
        case=c.execute('SELECT * FROM account_onboarding_cases WHERE tenant_id=%s AND case_id=%s FOR UPDATE',(TENANT,CASE)).fetchone()
        if not case or case['target_subject_id']!='alpha-world-t' or case['stage']!='historical_reporting' or case['status']!='in_progress': raise ValueError('T case stage changed; reconcile before recovery')
        batch=c.execute('SELECT * FROM onboarding_historical_report_batches WHERE tenant_id=%s AND batch_id=%s FOR UPDATE',(TENANT,BATCH)).fetchone()
        if not batch or batch['status']!='failed' or batch['error_message']!='T cannot use the MS or an unrelated completed design authority' or batch['rendered_artifact_count'] or batch['validated_artifact_count'] or batch['approved_at']: raise ValueError('Not the expected empty failed batch')
        if c.execute("SELECT count(*) AS n FROM onboarding_historical_report_batches WHERE tenant_id=%s AND subject_id='alpha-world-t' AND status<>'failed' AND status<>'superseded'",(TENANT,)).fetchone()['n']: raise ValueError('Another current T batch exists')
        subject=c.execute("SELECT lifecycle_status FROM platform_subjects WHERE tenant_id=%s AND subject_id='alpha-world-t' FOR UPDATE",(TENANT,)).fetchone()
        if subject['lifecycle_status']!='onboarding': raise ValueError('T is no longer private onboarding')
        if c.execute("SELECT count(*) AS n FROM account_onboarding_approvals WHERE tenant_id=%s AND subject_id='alpha-world-t' AND approval_type IN ('owner_activation','website_preview','historical_report_batch') AND approval_status='approved' AND revoked_at IS NULL",(TENANT,)).fetchone()['n']: raise ValueError('Later approval exists')
        items=list(c.execute('SELECT * FROM onboarding_historical_report_items WHERE tenant_id=%s AND batch_id=%s',(TENANT,BATCH)))
        if len(items)!=42 or any(r['artifact_sha256'] or r['generation_run_id'] or r['status']!='queued' for r in items): raise ValueError('Failed batch has registered output')
        gate=c.execute("SELECT * FROM account_onboarding_checks WHERE tenant_id=%s AND case_id=%s AND check_id='report_documents_validated' FOR UPDATE",(TENANT,CASE)).fetchone()
        if not gate or gate['status']!='pass': raise ValueError('Document gate changed')
        if c.execute('SELECT count(*) AS n FROM audit_events WHERE tenant_id=%s AND event_type=%s',(TENANT,EVENT)).fetchone()['n']: raise ValueError('Recovery already recorded')
        def guard():
            queries={
              'approvals':'SELECT * FROM account_onboarding_approvals WHERE tenant_id=%s',
              'subjects':'SELECT * FROM platform_subjects WHERE tenant_id=%s',
              'assets':'SELECT * FROM subject_report_template_assets WHERE tenant_id=%s',
              'batches':'SELECT * FROM onboarding_historical_report_batches WHERE tenant_id=%s',
              'items':'SELECT * FROM onboarding_historical_report_items WHERE tenant_id=%s',
              'snapshots':'SELECT * FROM reporting_snapshots WHERE tenant_id=%s',
              'nav':'SELECT * FROM subject_nav_series WHERE tenant_id=%s',
              'releases':'SELECT * FROM report_artifact_releases WHERE tenant_id=%s',
              'access':'SELECT * FROM user_account_access WHERE tenant_id=%s',
              'other_checks':"SELECT * FROM account_onboarding_checks WHERE tenant_id=%s AND NOT(case_id='"+CASE+"' AND check_id='report_documents_validated')",
            }
            return {key:digest(sorted(list(c.execute(sql,(TENANT,))),key=canonical)) for key,sql in queries.items()}
        protected=guard()
        before=retain('before',{'captured_at':datetime.now(timezone.utc).isoformat(),'case':case,'document_gate':gate,'failed_batch':batch,'protected':protected,'authority':'Owner explicitly approved reopening only T document validation, correcting the reference and rerunning the batch.'})
        def update():
            changed=c.execute("UPDATE account_onboarding_cases SET stage='template_intake',updated_at=now() WHERE tenant_id=%s AND case_id=%s AND stage='historical_reporting'",(TENANT,CASE)).rowcount
            if changed!=1: raise ValueError('Case recovery update mismatch')
            changed=c.execute("UPDATE account_onboarding_checks SET status='pending',message='Owner-authorized correction of failed T design-reference binding; revalidate the complete document package.',checked_at=now() WHERE tenant_id=%s AND case_id=%s AND check_id='report_documents_validated' AND status='pass'",(TENANT,CASE)).rowcount
            if changed!=1: raise ValueError('Gate recovery update mismatch')
        c.execute('SAVEPOINT recovery_rehearsal')
        update()
        if guard()!=protected: raise ValueError('Rehearsal changed protected data')
        c.execute('ROLLBACK TO SAVEPOINT recovery_rehearsal')
        restored=c.execute('SELECT * FROM account_onboarding_cases WHERE tenant_id=%s AND case_id=%s',(TENANT,CASE)).fetchone()
        restored_gate=c.execute("SELECT * FROM account_onboarding_checks WHERE tenant_id=%s AND case_id=%s AND check_id='report_documents_validated'",(TENANT,CASE)).fetchone()
        if restored!=case or restored_gate!=gate: raise ValueError('Rehearsal rollback did not restore exact state')
        update()
        if guard()!=protected: raise ValueError('Recovery changed protected data')
        evidence={'before':before,'failed_batch_id':BATCH,'old_stage':'historical_reporting','new_stage':'template_intake','rollback_verified':True,'protected_hashes':protected,'report_approvals_changed':False,'viewer_access_changed':False}
        c.execute("INSERT INTO audit_events(tenant_id,occurred_at,actor_type,actor_id,event_type,subject_id,outcome,details_json) VALUES (%s,%s,'system','owner-authorized-t-recovery',%s,'alpha-world-t','document_revalidation_required',%s)",(TENANT,datetime.now(timezone.utc).isoformat(),EVENT,canonical(evidence).decode()))
        c.commit()
        c.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
        actual=c.execute('SELECT stage FROM account_onboarding_cases WHERE tenant_id=%s AND case_id=%s',(TENANT,CASE)).fetchone()
        if actual['stage']!='template_intake' or guard()!=protected: raise ValueError('Committed readback differs; inspect before retry')
        c.rollback()
    evidence['status']='completed'; evidence['receipt']=retain('completed',evidence)
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps(evidence),flush=True)

if __name__=='__main__': main()
