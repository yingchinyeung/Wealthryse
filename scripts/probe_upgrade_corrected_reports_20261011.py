"""Render source-only candidates against read-only QA inputs, no registration."""
import hashlib, io, json, os, socket, subprocess, sys, tempfile, types
from pathlib import Path
import qa_read_only as q

STAGE='preflight'
CONTAINER='upgrade-g04-reports-20261010'
PREFIX='candidate-stage33-v1/'
STORAGE='https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net'

def sha(data):return hashlib.sha256(data).hexdigest()

def main():
    global STAGE
    if os.getuid()!=10001:raise ValueError('Non-root required')
    if {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Wrong recovery address')
    sys.path[:0]=['/app/src','/app/scripts']
    for name,source in PYTHON_CANDIDATES.items():
        module=types.ModuleType(name);module.__package__='ibkr_etl';sys.modules[name]=module
        exec(source,module.__dict__)
    from ibkr_etl.reporting_currency_holdings import normalize_collected_report_holdings
    from ibkr_etl.util import canonical_json
    from ibkr_etl.monthly_artifacts import render_factsheet
    from validate_subject_presentation import validate_subject_presentation
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient
    from pypdf import PdfReader
    template=Path(os.environ['PRESENTATION_TEMPLATE_PATH'])
    if sha(template.read_bytes())!='4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191':raise ValueError('Template drift')
    if os.environ['AZURE_STORAGE_ACCOUNT_URL']!=STORAGE:raise ValueError('Wrong storage')
    identity=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
    service=BlobServiceClient(STORAGE,credential=identity);bucket=service.get_container_client(CONTAINER)
    if bucket.get_container_properties().get('public_access') is not None:raise ValueError('Private QA outputs required')
    inventory={b.name:(str(b.etag),b.size) for b in bucket.list_blobs()}
    if any(n.startswith(PREFIX) for n in inventory):raise ValueError('Preserve earlier candidate attempt')
    results=[];uploads=[]
    with q.connect(q.TEST_DB) as db, tempfile.TemporaryDirectory(prefix='upgrade-candidate-') as temporary:
        if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only database required')
        class Adapter:
            def execute(self,sql,args=()):
                if not sql.lstrip().upper().startswith('SELECT '):raise ValueError('SELECT only')
                return db.execute(sql.replace('?', '%s'),args)
        def fingerprints():
            return {t:sha('\n'.join(r['h'] for r in db.execute('SELECT md5(row_to_json(t)::text) AS h FROM '+t+' t ORDER BY 1').fetchall()).encode())
                    for t in ('reporting_snapshots','report_generation_runs','report_artifact_releases','report_release_decisions')}
        before=fingerprints();rows=db.execute('SELECT subject_id,data_sha256,snapshot_payload_json FROM reporting_snapshots ORDER BY subject_id').fetchall()
        if {r['subject_id']:r['data_sha256'] for r in rows}!=EXPECTED_HASHES:raise ValueError('Frozen QA drift')
        root=Path(temporary)
        for name,source in JS_CANDIDATES.items():
            if Path(name).name!=name or not name.endswith('.mjs'):raise ValueError('Bad source name')
            (root/name).write_text(source,encoding='utf-8')
        modules=next((p for p in [Path('/app/scripts/node_modules'),Path('/app/node_modules')] if (p/'@oai/artifact-tool/package.json').is_file()),None)
        if modules is None:raise ValueError('Installed artifact runtime missing')
        (root/'node_modules').symlink_to(modules,target_is_directory=True)
        for row in rows:
            subject=row['subject_id'];STAGE='input:'+subject
            raw=row['snapshot_payload_json']
            if sha(raw.encode())!=row['data_sha256']:raise ValueError('Frozen payload hash mismatch')
            p=json.loads(raw);original=json.loads(raw)
            if subject in ('U16088571','U16088606','hong-kong-china'):
                data=normalize_collected_report_holdings(Adapter(),subject,p['reporting_currency'],
                    {'holdings':{'as_of':p['data_as_of'],'data':p['holdings']},'sector_allocation':{'data':p['sector_allocation']}})
                for key in ('holdings','sector_allocation'):
                    p[key]=data[key]['data'];p['source_citations'][key]+=data[key].get('citations',[])
            for key in original:
                if key not in ('holdings','sector_allocation','source_citations') and p[key]!=original[key]:raise ValueError('Unexpected financial data change')
            strategy=None
            if subject!='U16088571':
                rows_identity=db.execute("SELECT scope_json,scope_sha256 FROM account_onboarding_approvals WHERE subject_id=%s AND approval_type='presentation_identity' AND approval_status='approved'",(subject,)).fetchall()
                if len(rows_identity)!=1:raise ValueError('Unique approved identity required')
                scope=rows_identity[0]['scope_json'];scope=json.loads(scope) if isinstance(scope,str) else scope
                if sha(canonical_json(scope).encode())!=rows_identity[0]['scope_sha256']:raise ValueError('Identity integrity mismatch')
                strategy=scope['strategy_name']
            folder=root/subject;folder.mkdir();snapshot=folder/'snapshot.json';snapshot.write_text(canonical_json(p),encoding='utf-8')
            assets={}
            if subject=='U16088571':
                assets['template']={'container_name':'internal-reports','blob_name':'templates/U16088571/monthly-factsheet/approved-august-2026-v2.pdf','sha256':'460064f7b7cd0650189e71949f31859e07f1fa7256f266c9138b9b9214ba705d'}
            else:
                for r in db.execute("SELECT asset_role,container_name,blob_name,sha256 FROM subject_report_template_assets WHERE subject_id=%s AND report_type='factsheet' AND asset_role IN ('template','completed_example') AND is_current AND approval_status IN ('validated','active') ORDER BY updated_at DESC",(subject,)).fetchall():assets.setdefault(r['asset_role'],dict(r))
                if set(assets)!={'template','completed_example'}:raise ValueError('Approved factsheet pair required')
            for role,asset in assets.items():
                if asset['container_name']!='internal-reports':raise ValueError('Unexpected template container')
                content=service.get_blob_client(asset['container_name'],asset['blob_name']).download_blob().readall()
                if sha(content)!=asset['sha256']:raise ValueError('Factsheet template hash differs')
                (folder/(role+'.pdf')).write_bytes(content)
            STAGE='factsheet:'+subject;pdf=folder/'factsheet.pdf'
            render_factsheet(p,template_path=folder/'template.pdf',design_model_path=folder/'completed_example.pdf' if 'completed_example' in assets else None,output_path=pdf)
            if len(PdfReader(pdf).pages)!=2:raise ValueError('Expected two-page factsheet')
            STAGE='presentation:'+subject;ppt=folder/'presentation.pptx'
            script=Path('/app/scripts/render_alpha_t_presentation.mjs') if subject=='alpha-world-t' else root/'render_monthly_presentation.mjs'
            env={k:v for k,v in os.environ.items() if not any(s in k.upper() for s in ('DATABASE','SECRET','TOKEN','PASSWORD','AZURE_OPENAI'))}
            env.pop('PRESENTATION_STRATEGY_NAME',None)
            if strategy:env['PRESENTATION_STRATEGY_NAME']=strategy
            run=subprocess.run([os.environ.get('NODE_EXECUTABLE','node'),str(script),str(template),str(snapshot),str(ppt)],cwd='/app',env=env,capture_output=True,text=True)
            if run.returncode:
                # Diagnostics are private QA output; no resolved credentials are passed to Node.
                diagnostic=run.stderr[-12000:].encode();name=PREFIX+subject+'/render-error.txt'
                bucket.upload_blob(name,diagnostic,overwrite=False);uploads.append(name)
                raise ValueError('Candidate presentation renderer failed; inspect private diagnostic')
            STAGE='validation:'+subject
            validation=validate_subject_presentation(ppt,snapshot,subject_id=subject,period_end=p['period_end'],expected_strategy=strategy)
            artifacts=[]
            for kind,file in (('factsheet',pdf),('presentation',ppt)):
                content=file.read_bytes();name=PREFIX+subject+'/'+file.name
                bucket.upload_blob(name,content,overwrite=False);uploads.append(name)
                if sha(bucket.download_blob(name).readall())!=sha(content):raise ValueError('Candidate readback mismatch')
                artifacts.append({'report_type':kind,'output_reference':CONTAINER+'/'+name,'sha256':sha(content)})
            result={'subject':subject,'source_snapshot_sha256':row['data_sha256'],'candidate_payload_sha256':sha(snapshot.read_bytes()),
                    'strategy':strategy,'artifacts':artifacts,'validation_status':validation['status'],'contract':validation['content_contract_version'],
                    'nav_and_performance_unchanged':True,'cash_other_internal':p['monthly_reporting']['cash_activity'].get('other_internal_cash'),
                    'gross_holdings':p['sector_allocation']['gross_market_value'],'reporting_currency':p['reporting_currency']}
            results.append(result);print('UPGRADE_CORRECTED_SUBJECT '+json.dumps(result),flush=True)
        if fingerprints()!=before:raise ValueError('QA state changed')
    after={b.name:(str(b.etag),b.size) for b in bucket.list_blobs()}
    if any(after.get(n)!=v for n,v in inventory.items()) or set(after)-set(inventory)!=set(uploads):raise ValueError('QA output scope differs')
    service.close();identity.close()
    print('UPGRADE_CORRECTED_FINAL '+json.dumps({'passed':True,'subjects':results,'artifacts':10,'qa_state_unchanged':True,
        'database_writes':0,'registered_reports':0,'production_connection_used':False,'deployed':False,
        'native_visual_acceptance':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED'}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('UPGRADE_CORRECTED_ERROR '+json.dumps({'type':type(e).__name__,'stage':STAGE,'message':str(e)[:600],'reconcile_before_retry':True}),flush=True)
        raise SystemExit(1)
