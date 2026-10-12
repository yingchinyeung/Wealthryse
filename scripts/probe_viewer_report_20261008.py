"""Private read-only reproduction; no model calls, source writes or report release."""
import asyncio
import csv
import hashlib
import io
import json
import os
from datetime import datetime, timezone
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
import psycopg
from psycopg.rows import dict_row

os.environ['PGOPTIONS'] = '-c default_transaction_read_only=on -c statement_timeout=120000'
from ibkr_etl import web
from ibkr_etl.agent_tools import AgentToolContext, execute_tool
from run_onboarded_viewer_access import asgi_get
from collect_operational_evidence import collect

def main():
    assert web.AUTHORIZATION_MODE == 'database'
    result = {'captured_at':datetime.now(timezone.utc).isoformat(),'database_writes':0,'model_calls':0,'gateway_tested':False,'checks':[]}
    with psycopg.connect(os.environ['DATABASE_URL'],options=os.environ['PGOPTIONS'],row_factory=dict_row) as connection:
        connection.execute("SELECT set_config('app.tenant_id',%s,true)",(web.TENANT_ID,))
        connection.execute('SET TRANSACTION READ ONLY')
        assert connection.execute("SELECT current_setting('transaction_read_only') AS value").fetchone()['value']=='on'
        result['operational_metadata'] = collect(connection)
        viewers = connection.execute("SELECT DISTINCT u.entra_object_id,u.email FROM app_users u JOIN app_user_roles r ON r.tenant_id=u.tenant_id AND r.entra_object_id=u.entra_object_id WHERE u.tenant_id=%s AND u.status='active' AND r.role_name='viewer' AND r.role_status='active' AND r.effective_from<=now() AND (r.effective_to IS NULL OR r.effective_to>=now()) ORDER BY u.entra_object_id",(web.TENANT_ID,)).fetchall()
        result['subjects'] = connection.execute('SELECT subject_id,subject_type,source_account_id,display_name,base_currency,reporting_currency FROM platform_subjects WHERE tenant_id=%s AND lifecycle_status=\'active\' ORDER BY subject_id',(web.TENANT_ID,)).fetchall()
        result['source_trade_counts'] = connection.execute('SELECT account_id,COUNT(*) AS rows,MIN(trade_date) AS first_date,MAX(trade_date) AS latest_date FROM trades GROUP BY account_id ORDER BY account_id').fetchall()
    for viewer in viewers:
        identity = {k:str(v) for k,v in viewer.items()}
        identity_hash = hashlib.sha256(identity['entra_object_id'].encode()).hexdigest()
        user={'object_id':identity['entra_object_id'],'email':identity['email'],'role':'viewer','roles':['viewer'],'authorization_source':'database'}
        allowed = web._authorized_subject_ids(user,feature_key='ai_analyst')
        for subject in result['subjects']:
            sid=subject['subject_id']
            for endpoint in ('summary','performance','holdings','cash','trades','risk','benchmark-status','benchmark-comparison','export/performance.csv','export/holdings.csv','export/cash-activity.csv','export/trades.csv'):
                record={'identity_sha256':identity_hash,'subject_id':sid,'endpoint':endpoint}
                try:
                    status,body=asyncio.run(asgi_get(web.app,f'/api/portfolio/{sid}/{endpoint}',identity))
                    record.update(status=status,bytes=len(body))
                    if endpoint.endswith('.csv') and status==200:
                        record['csv_rows']=max(0,len(list(csv.reader(io.StringIO(body.decode()))))-1)
                    elif status==200:
                        value=json.loads(body)
                        record['keys']=list(value)
                        for key in ('not_applicable','message','date_from','date_to','total_count','returned_count','currency','as_of','cache_status'):
                            if key in value: record[key]=value[key]
                        if endpoint=='summary': record['coverage']=value.get('nav_coverage')
                        if endpoint=='performance': record['observations']=len(value.get('series',[]))
                        if endpoint=='risk': record['metrics']= {k:v for k,v in (value.get('metrics') or {}).items() if 'observations' in k or 'period' in k};record['quality']=value.get('quality')
                    else:
                        record['detail']=json.loads(body).get('detail')
                except Exception as error:
                    record['error_type']=type(error).__name__
                result['checks'].append(record)
            context=AgentToolContext(tenant_id=web.TENANT_ID,actor_object_id=identity['entra_object_id'],allowed_subject_ids=frozenset(allowed),correlation_id='viewer-read-only-diagnostic')
            record={'identity_sha256':identity_hash,'subject_id':sid,'endpoint':'analyst_grounding_tools','feature_authorized':sid in allowed}
            try:
                with web._database() as connection:
                    outputs=[execute_tool(connection,context,name,{'subject_id':sid}) for name in ('get_portfolio_summary','get_performance','get_risk_metrics')]
                record['passed']=True;record['output_tools']=[o.get('tool_name') for o in outputs]
                from ibkr_etl.ai_analyst import _deterministic_outputs
                _,charts=_deterministic_outputs(outputs,{'results':[]})
                nav_chart=next(chart for chart in charts if chart['id']=='nav-history')
                if nav_chart['unit']!=subject['reporting_currency'] or nav_chart['y_label']!='NAV ('+subject['reporting_currency']+')':raise ValueError('Chart reporting currency differs')
                record['nav_chart_currency']=nav_chart['unit'];record['nav_chart_label']=nav_chart['y_label']
            except Exception as error:
                record.update(passed=False,error_type=type(error).__name__,error_code=getattr(error,'code',None))
            result['checks'].append(record)
    data=json.dumps(result,default=str,sort_keys=True).encode()
    digest=hashlib.sha256(data).hexdigest()
    blob=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential()).get_blob_client('internal-reports','review/viewer-report-investigation-20261008/probe-v1.json')
    blob.upload_blob(data,overwrite=False,metadata={'sha256':digest},content_settings=ContentSettings(content_type='application/json'))
    print(json.dumps({'status':'read_only_probe_complete','checks':len(result['checks']),'sha256':digest,'database_writes':0}),flush=True)

if __name__=='__main__': main()
