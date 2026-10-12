"""Render one subject's 21 private historical pairs with the installed image."""
import hashlib,json,os,socket,subprocess,sys,tempfile
from pathlib import Path
import prepare_reporting_migration_clone_20261011 as q
from audit_generic_report_numbers import audit as generic_audit
STAGE='boundary';CONTAINER='upgrade-g04-reports-20261010'
STORAGE='https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net'
def sha(content):return hashlib.sha256(content).hexdigest()

def main():
    global STAGE
    if os.getuid()!=10001 or q.TEST_DB!='upgrade_g04_migration_20261011' or SUBJECT not in ('U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'):raise ValueError('Exact historical QA boundary required')
    if {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Private recovery endpoint required')
    for name,digest in SOURCE_HASHES.items():
        if sha((Path('/app')/name).read_bytes())!=digest:raise ValueError('Installed source drift')
    from ibkr_etl.util import canonical_json
    from ibkr_etl.monthly_artifacts import render_factsheet
    from validate_subject_presentation import validate_subject_presentation
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient
    from pypdf import PdfReader
    template=Path(os.environ['PRESENTATION_TEMPLATE_PATH'])
    if sha(template.read_bytes())!='4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191' or os.environ['AZURE_STORAGE_ACCOUNT_URL']!=STORAGE:raise ValueError('Approved template/storage drift')
    prefix='candidate-stage34-historical-v1/'+SUBJECT+'/'
    results=[];uploads=[]
    with q.connect(q.TEST_DB) as db,ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID']) as identity,BlobServiceClient(STORAGE,credential=identity) as service,tempfile.TemporaryDirectory(prefix='historical-report-') as folder:
        if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
        def state():return {t:sha('\n'.join(r['h'] for r in db.execute('SELECT md5(row_to_json(t)::text) AS h FROM '+t+' t ORDER BY 1').fetchall()).encode()) for t in q.REPORT_TABLES}
        before=state()
        if before!=EXPECTED_REPORT_HASHES:raise ValueError('Migrated QA report state differs')
        bucket=service.get_container_client(CONTAINER)
        if bucket.get_container_properties().get('public_access') is not None:raise ValueError('Private QA output required')
        inventory={b.name:(str(b.etag),b.size) for b in bucket.list_blobs()}
        if any(n.startswith(prefix) for n in inventory):raise ValueError('Preserve previous historical attempt')
        rows=db.execute("SELECT snapshot_id,period_end,data_sha256,snapshot_payload_json FROM reporting_snapshots WHERE tenant_id=%s AND subject_id=%s AND status IN ('frozen','approved') ORDER BY period_end",(q.TENANT,SUBJECT)).fetchall()
        if len(rows)!=21 or {r['period_end']:r['data_sha256'] for r in rows}!=EXPECTED_HASHES:raise ValueError('Exactly 21 hash-bound periods required')
        strategy=None
        if SUBJECT!='U16088571':
            approved=db.execute("SELECT scope_json,scope_sha256 FROM account_onboarding_approvals WHERE subject_id=%s AND approval_type='presentation_identity' AND approval_status='approved'",(SUBJECT,)).fetchall()
            if len(approved)!=1:raise ValueError('Unique approved identity required')
            scope=approved[0]['scope_json'];scope=json.loads(scope) if isinstance(scope,str) else scope
            if sha(canonical_json(scope).encode())!=approved[0]['scope_sha256']:raise ValueError('Identity hash differs')
            strategy=scope['strategy_name']
        assets={}
        if SUBJECT=='U16088571':assets['template']={'container_name':'internal-reports','blob_name':'templates/U16088571/monthly-factsheet/approved-august-2026-v2.pdf','sha256':'460064f7b7cd0650189e71949f31859e07f1fa7256f266c9138b9b9214ba705d'}
        else:
            for r in db.execute("SELECT asset_role,container_name,blob_name,sha256 FROM subject_report_template_assets WHERE subject_id=%s AND report_type='factsheet' AND asset_role IN ('template','completed_example') AND is_current AND approval_status IN ('validated','active') ORDER BY updated_at DESC",(SUBJECT,)).fetchall():assets.setdefault(r['asset_role'],dict(r))
            if set(assets)!={'template','completed_example'}:raise ValueError('Approved factsheet pair required')
        root=Path(folder)
        for role,asset in assets.items():
            if service.get_container_client(asset['container_name']).get_container_properties().get('public_access') is not None:raise ValueError('Private template required')
            content=service.get_blob_client(asset['container_name'],asset['blob_name']).download_blob().readall()
            if sha(content)!=asset['sha256']:raise ValueError('Template integrity differs')
            (root/(role+'.pdf')).write_bytes(content)
        for row in rows:
            period=row['period_end'];raw=row['snapshot_payload_json'];STAGE='input:'+period
            if sha(raw.encode())!=row['data_sha256']:raise ValueError('Frozen payload integrity differs')
            p=json.loads(raw)
            if p['subject_id']!=SUBJECT or p['period_end']!=period:raise ValueError('Frozen identity differs')
            work=root/period;work.mkdir();snapshot=work/'snapshot.json';snapshot.write_text(raw,encoding='utf-8')
            pdf=work/'factsheet.pdf';ppt=work/'presentation.pptx';STAGE='factsheet:'+period
            render_factsheet(p,template_path=root/'template.pdf',design_model_path=root/'completed_example.pdf' if 'completed_example' in assets else None,output_path=pdf)
            if len(PdfReader(pdf).pages)!=2:raise ValueError('Exactly two factsheet pages required')
            STAGE='presentation:'+period
            script='/app/scripts/render_alpha_t_presentation.mjs' if SUBJECT=='alpha-world-t' else '/app/scripts/render_monthly_presentation.mjs'
            env={k:v for k,v in os.environ.items() if not any(s in k.upper() for s in ('DATABASE','SECRET','TOKEN','PASSWORD','AZURE_OPENAI'))}
            env.pop('PRESENTATION_STRATEGY_NAME',None)
            if strategy:env['PRESENTATION_STRATEGY_NAME']=strategy
            run=subprocess.run([os.environ.get('NODE_EXECUTABLE','node'),script,str(template),str(snapshot),str(ppt)],cwd='/app',env=env,capture_output=True,text=True,timeout=240)
            if run.returncode:
                name=prefix+period+'/render-error.txt';bucket.upload_blob(name,run.stderr[-12000:].encode(),overwrite=False)
                raise ValueError('Historical renderer failed; private diagnostic retained')
            STAGE='validation:'+period
            validation=validate_subject_presentation(ppt,snapshot,subject_id=SUBJECT,period_end=period,expected_strategy=strategy)
            if SUBJECT in ('U16088571','U16088606','hong-kong-china'):financial=generic_audit(ppt,p)
            else:financial=validation['ms_financial_audit'] if SUBJECT=='alpha-world-ms' else validation['t_financial_audit']
            if financial['status']!='passed' or financial['financial_charts_verified']!=6:raise ValueError('Full six-chart financial audit required')
            if snapshot.read_bytes()!=raw.encode():raise ValueError('Input changed during render')
            artifacts=[]
            for kind,path in (('factsheet',pdf),('presentation',ppt)):
                content=path.read_bytes();name=prefix+period+'/'+path.name;bucket.upload_blob(name,content,overwrite=False);uploads.append(name)
                if sha(bucket.download_blob(name).readall())!=sha(content):raise ValueError('Private output readback differs')
                artifacts.append({'report_type':kind,'output_reference':CONTAINER+'/'+name,'sha256':sha(content)})
            result={'subject':SUBJECT,'period_end':period,'snapshot_id':str(row['snapshot_id']),'snapshot_sha256':row['data_sha256'],'contract':validation['content_contract_version'],
                'financial_status':financial['status'],'charts_verified':6,'risk_metrics_verified':financial['risk_metrics_verified'],'artifacts':artifacts}
            results.append(result)
            print('REPORTING_HISTORICAL_MONTH '+json.dumps({'subject':SUBJECT,'period_end':period,'passed':True,'completed_months':len(results)}),flush=True)
        if state()!=before:raise ValueError('QA report metadata changed')
        receipt=canonical_json({'subject':SUBJECT,'image':IMAGE,'months':results,'database_read_only':True,'native_visual_review':'UNVERIFIED'}).encode()
        receipt_name=prefix+'receipt.json';bucket.upload_blob(receipt_name,receipt,overwrite=False);uploads.append(receipt_name)
        if sha(bucket.download_blob(receipt_name).readall())!=sha(receipt):raise ValueError('Receipt readback differs')
        after={b.name:(str(b.etag),b.size) for b in bucket.list_blobs()}
        if any(after.get(n)!=v for n,v in inventory.items()) or set(after)-set(inventory)!=set(uploads):raise ValueError('Output write scope differs')
    print('REPORTING_HISTORICAL_FINAL '+json.dumps({'passed':True,'subject':SUBJECT,'months':21,'artifacts':42,'slides_structurally_checked':420,'financial_charts_verified':126,
        'receipt_blob':receipt_name,'receipt_sha256':sha(receipt),'database_writes':0,'report_registrations':0,'approvals_changed':False,'production_connection_used':False,
        'native_visual_review':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED'}),flush=True)
if __name__=='__main__':
    try:main()
    except Exception as e:
        print('REPORTING_HISTORICAL_ERROR '+json.dumps({'type':type(e).__name__,'stage':STAGE,'message':str(e)[:450],'reconcile_before_retry':True}),flush=True)
        raise SystemExit(1)
