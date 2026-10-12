"""Dispatch exact, separately gated stages without altering persistent Azure settings."""
import hashlib,json,sys,base64,zlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'.work/alpha-ms-azure-controller-runtime')]
import run_alpha_ms_recovery_operation as ctl
ctl.FOLDER=ROOT/'.work/alpha-t-cash-backfill-20261009-r2'
SCRIPT='backfill_alpha_t_cash_20261009.py'
def main():
    ctl.FOLDER.mkdir(exist_ok=True)
    action,mode=sys.argv[1:3]
    if mode in ('diagnose','diagnose-release','diagnose-release-r2'):
        if action=='start':
            helper=base64.b64encode(zlib.compress((ROOT/'scripts'/SCRIPT).read_bytes())).decode()
            ctl.start(mode,'ibkrv2stg-onboard-render','diagnose_alpha_t_cash_backfill.py',{'IBKR_RUN_MIGRATIONS':'false','T_CASH_HELPER':helper})
        elif action=='status':ctl.read(mode)
        elif action=='download':
            from azure.identity import AzureCliCredential
            from azure.storage.blob import BlobServiceClient
            proof=json.loads((ctl.FOLDER/(mode+'-result.json')).read_bytes())
            if proof['status']!='Succeeded' or not proof['job_settings_unchanged']:raise ValueError('Diagnostic job not verified')
            row=next(r for r in proof['records'] if r.get('status')=='diagnosed')
            data=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()).get_blob_client('internal-reports',row['blob']).download_blob().readall()
            if hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Diagnostic hash mismatch')
            with (ctl.FOLDER/(mode+'-evidence.json')).open('xb') as f:f.write(data)
            print(json.dumps({'downloaded':mode,'sha256':row['sha256']}))
        else:raise ValueError('Use start or status for read-only diagnosis')
        return
    if mode not in ('rehearse','apply','verify'):raise ValueError('Invalid stage')
    if action=='start':
        digest=hashlib.sha256((ROOT/'scripts'/SCRIPT).read_bytes()).hexdigest()
        env={'IBKR_RUN_MIGRATIONS':'false','T_CASH_MODE':mode,'T_CASH_CODE_SHA':digest}
        if mode!='rehearse':
            stage='rehearse' if mode=='apply' else 'apply'
            proof=json.loads((ctl.FOLDER/(stage+'-result.json')).read_bytes())
            if proof['status']!='Succeeded' or not proof['job_settings_unchanged']:raise ValueError('Previous job failed')
            records=[r for r in proof['records'] if r.get('passed') is True and r.get('mode')==stage]
            if len(records)!=1:raise ValueError('Ambiguous prerequisite')
            env['T_CASH_PRIOR_SHA']=records[0]['sha256']
        ctl.start(mode,'ibkrv2stg-onboard-render',SCRIPT,env)
    elif action=='status':ctl.read(mode)
    elif action=='download':
        from azure.identity import AzureCliCredential
        from azure.storage.blob import BlobServiceClient
        service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential())
        receipt_path=ctl.FOLDER/(mode+'-result.json')
        if not receipt_path.exists():
            # Log Analytics delivery can lag. Verify the immutable receipt only
            # after the exact native execution reports success and settings match.
            ack=json.loads((ctl.FOLDER/(mode+'-dispatch.json')).read_bytes())
            intent=json.loads((ctl.FOLDER/(mode+'-intent.json')).read_bytes())
            route=ctl.BASE+'Microsoft.App/jobs/'+ack['job']
            run=ctl.request('GET',route+'/executions/'+ack['execution']+'?api-version=2024-03-01')
            if run['properties']['status']!='Succeeded' or ctl.invariant(ctl.request('GET',route+'?api-version=2024-03-01'))!=intent['job_invariant']:raise ValueError('Native execution not verified')
            name='review/alpha-world-t/cash-recovery-20261009-r2/'+mode+'.json'
            data=service.get_blob_client('internal-reports',name).download_blob().readall();out=json.loads(data)
            if out['mode']!=mode or out['passed'] is not True or out['source_code_sha256']!=intent['source_sha256']:raise ValueError('Receipt binding differs')
            r={'passed':True,'mode':mode,'blob':name,'sha256':hashlib.sha256(data).hexdigest()}
            ctl.save(mode+'-result',{'status':'Succeeded','job_settings_unchanged':True,'records':[r],'evidence_source':'native execution plus immutable receipt',**ack})
        proof=json.loads(receipt_path.read_bytes())
        r=next(r for r in proof['records'] if r.get('passed'))
        data=service.get_blob_client('internal-reports',r['blob']).download_blob().readall()
        if hashlib.sha256(data).hexdigest()!=r['sha256']:raise ValueError('Hash mismatch')
        with (ctl.FOLDER/(mode+'-evidence.json')).open('xb') as f:f.write(data)
        print(json.dumps({'downloaded':mode,'sha256':r['sha256']}))
    else:raise ValueError('Invalid action')
if __name__=='__main__':main()
