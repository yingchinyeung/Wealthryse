"""Read-only normal-producer and all-month native report qualification."""
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from uuid import uuid5
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient,ContentSettings
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
from ibkr_etl.util import canonical_json
from ibkr_etl.reporting_snapshot import active_month_end_snapshot_id,SNAPSHOT_NAMESPACE
from ibkr_etl.alpha_world_ms_crisis import load,qualified_snapshot,validate
from ibkr_etl.alpha_world_ms_analytics import analyze
from ibkr_etl.agent_tools import _risk
from ibkr_etl.monthly_artifacts import render_factsheet
from ibkr_etl.alpha_world_ms_pdf_validation import validate as validate_pdf
from generate_month_end_package import _load_frozen_snapshot
from render_onboarding_historical_reports import _render_presentation
from ibkr_etl.internal_release import approve_internal_release

TENANT='00000000-0000-0000-0000-000000000001';SUBJECT='alpha-world-ms'
def sha(data):return hashlib.sha256(data).hexdigest()
def require(ok,message):
    if not ok:raise ValueError(message)

def main():
    b=PROBE_BINDING
    for name,digest in b['installed_files_sha256'].items():require(sha(Path('/app',name).read_bytes())==digest,'Installed source changed: '+name)
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    def put(name,data,mime):
        client=service.get_blob_client('internal-reports',b['prefix']+name)
        client.upload_blob(data,overwrite=False,content_settings=ContentSettings(content_type=mime),metadata={'sha256':sha(data)})
        require(client.download_blob().readall()==data,'Private artifact readback failed')
        return b['prefix']+name
    rows=[]
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,connect_timeout=25,
                        options='-c default_transaction_read_only=on -c statement_timeout=120000') as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');raw.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
        require(raw.execute('SHOW transaction_read_only').fetchone()[0]=='on','Read-only transaction required')
        c=PostgresConnection(raw)
        assets=[dict(r) for r in c.execute("SELECT report_type,asset_role,container_name,blob_name,sha256 FROM subject_report_template_assets WHERE tenant_id=? AND subject_id=? AND report_type='factsheet' AND is_current AND approval_status='validated'",(TENANT,SUBJECT))]
        require(len(assets)==2 and {r['asset_role'] for r in assets}=={'template','completed_example'},'Validated template pair required')
        template=Path('/app/templates/U16088571/approved-august-2026-20-slide-v24.pptx')
        require(sha(template.read_bytes())=='4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191','Approved presentation source changed')
        with TemporaryDirectory(prefix='ms-crisis-read-only-') as temporary:
            folder=Path(temporary)
            for asset in assets:
                data=service.get_blob_client(asset['container_name'],asset['blob_name']).download_blob().readall()
                require(sha(data)==asset['sha256'],'Authority template changed');(folder/(asset['asset_role']+'.pdf')).write_bytes(data)
            for wanted in b['months']:
                period=wanted['period_end'];snapshot_id=active_month_end_snapshot_id(c,tenant_id=TENANT,subject_id=SUBJECT,period_end=period)
                p,digest=_load_frozen_snapshot(c,tenant_id=TENANT,account_id=SUBJECT,snapshot_id=snapshot_id)
                require(snapshot_id==wanted['original_snapshot_id'] and digest==wanted['original_sha256'],'Frozen report input drifted')
                replay=load(c,p,tenant_id=TENANT)
                require(abs(replay['maximum_drawdown']-wanted['maximum_drawdown'])<2e-12,'Actual source stress differs from accepted local arithmetic')
                # Invoke the normal producer directly, without audit INSERTs or
                # dispatching ordinary jobs. This exercises raw-connection adapter.
                actual=_risk(raw,SUBJECT,period,{}).data
                require(actual['metrics']==p['risk']['metrics'] and actual['crisis_replay'] is not None,'Future normal producer failed')
                require(abs(actual['crisis_replay']['maximum_drawdown']-replay['maximum_drawdown'])<2e-12,'Normal producer stress differs')
                new=qualified_snapshot(p,replay);require(analyze(p)==analyze(new),'Original NAV/HIBOR metrics changed')
                payload=canonical_json(new).encode();newsha=sha(payload)
                newid=str(uuid5(SNAPSHOT_NAMESPACE,f'{TENANT}:{SUBJECT}:{period}:{newsha}'))
                payload_blob=put('inputs/'+period+'.json',payload,'application/json')
                source=folder/(period+'.json');source.write_bytes(payload)
                pdf=folder/('AlphaWorld_MS_Factsheet_'+period+'.pdf')
                render_factsheet(new,template_path=folder/'template.pdf',design_model_path=folder/'completed_example.pdf',output_path=pdf);validate_pdf(pdf,new)
                pptx=folder/('AlphaWorld_MS_Presentation_'+period+'.pptx')
                validation=_render_presentation(template=template,snapshot=source,output=pptx,period_end=period,subject_id=SUBJECT,expected_strategy='iStrategy: Alpha World MS')
                require(validation['ms_financial_audit']['qualified_crisis_verified'],'Exported crisis results not verified')
                artifacts={}
                for kind,path,mime in (('factsheet',pdf,'application/pdf'),('presentation',pptx,'application/vnd.openxmlformats-officedocument.presentationml.presentation')):
                    content=path.read_bytes();artifacts[kind+'_sha256']=sha(content);artifacts[kind+'_blob']=put('outputs/'+path.name,content,mime)
                approval=approve_internal_release({'snapshot_id':newid,'subject_id':SUBJECT,'period_end':period,'status':'frozen','quality_status':'warn',
                    'publication_blockers_json':new['publication_blockers']},[{'report_type':kind,'snapshot_id':newid,'generation_run_id':i,'status':'awaiting_approval',
                    'output_sha256':artifacts[kind+'_sha256']} for i,kind in enumerate(('factsheet','presentation'))],user_acceptance_recorded=True)
                rows.append({**wanted,**artifacts,'snapshot_id':newid,'data_sha256':newsha,'payload_blob':payload_blob,
                    'validation':validation,'remaining_blockers':list(approval.blockers),'normal_producer_verified':True,'metrics_unchanged':True})
                print(json.dumps({'period_end':period,'qualified':True}),flush=True)
        raw.rollback()
    result={'status':'passed','image':b['image'],'count':len(rows),'artifacts':rows,'database_writes':0,
            'read_only_transaction_enforced':True,'reports_released':False,'source_data_changed':False,
            'report_approvals_changed':False,'normal_producer_verified':True}
    put('verification.json',json.dumps(result,sort_keys=True,indent=2,allow_nan=False).encode(),'application/json')
    print(json.dumps({'status':'passed','count':len(rows),'database_writes':0}),flush=True)
if __name__=='__main__':main()
