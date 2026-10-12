"""Read-only real-cache display acceptance with synthetic authorization tests."""
import hashlib,io,json,os,subprocess,sys,unittest
from pathlib import Path
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=30000'
os.environ['PORTAL_TEST_RUNTIME']='/app'
os.environ['RISK_DISPLAY_SOURCE']='/app/src/ibkr_etl/risk_display.py'
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl import web
from ibkr_etl.risk_display import RISK_DISPLAY_SCRIPT
from ibkr_etl.risk_snapshots import snapshot_read_status,decoded_payload
from fastapi.testclient import TestClient

def main():
    cases=unittest.TestSuite()
    for pattern in ('test_snapshot_access_correction.py','test_risk_display.py'):
        cases.addTests(unittest.defaultTestLoader.discover('/app/upgrade-tests',pattern=pattern))
    tests=unittest.TextTestRunner(stream=io.StringIO()).run(cases)
    if not tests.wasSuccessful() or tests.testsRun!=26:raise ValueError('Display/access regression failed')
    rows=[]
    with web._database() as connection:
        for subject in ('alpha-world-ms','alpha-world-t','hong-kong-china','U16088571','U16088606'):
            result=connection.execute('''SELECT payload_json,payload_sha256,quality_status,comparison_version,as_of_date
                FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id=%s AND analytics_version=%s
                ORDER BY as_of_date DESC LIMIT 1''',(web.TENANT_ID,subject,'risk-analytics-v2')).fetchone()
            if not result:raise ValueError('Risk snapshot absent')
            payload=decoded_payload(result['payload_json'])
            payload.pop('source_snapshot',None)
            code=RISK_DISPLAY_SCRIPT+"\nconst input=JSON.parse(require('fs').readFileSync(0,'utf8'));console.log(JSON.stringify(riskDisplayNotes(input,v=>v==null?'—':String(v))));"
            run=subprocess.run(['node','-e',code],input=json.dumps(payload),capture_output=True,text=True,timeout=30)
            if run.returncode:raise ValueError('Actual display JavaScript failed')
            labels=json.loads(run.stdout)
            if 'undefined' in labels['risk'] or 'NaN' in labels['risk']:raise ValueError('Undefined display metadata')
            if subject in ('alpha-world-ms','alpha-world-t'):
                if 'USD positions' in labels['attribution'] or 'NAV valuations: 0' in labels['risk']:
                    raise ValueError('Composite display mismatch')
            rows.append({'subject':subject,'as_of':str(result['as_of_date']),'labels':labels})
    html=web._render_account_page({'email':'synthetic@example.invalid','role':'owner'},account_id='alpha-world-t',
        section='risk',access={'display_name':'Alpha World T','reporting_currency':'HKD',
        'history_start_date':'2025-01-20','preview_mode':False})
    if RISK_DISPLAY_SCRIPT not in html or 'textContent=displayNotes.risk' not in html:
        raise ValueError('Tested helper is not wired into actual page')
    ready=TestClient(web.app).get('/readyz').status_code
    if ready!=200:raise ValueError('Database readiness failed')
    record={'passed':True,'tests_run':tests.testsRun,'read_only':True,'database_writes':0,'model_calls':0,
        'actual_viewer_session':False,'readiness_status':ready,'display_records':rows,
        'latest_source_freshness_qualified':False,
        'source_sha256':{n:hashlib.sha256(Path('/app/src/ibkr_etl',n).read_bytes()).hexdigest() for n in ('web.py','risk_display.py')}}
    print('PLATFORM_CORRECTION_PROBE '+json.dumps(record),flush=True)

if __name__=='__main__':main()
