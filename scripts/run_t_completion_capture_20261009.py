"""Controlled read-only evidence capture; no persistent Azure setting changes."""
import json,hashlib,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'.work/alpha-ms-azure-controller-runtime')]
import run_alpha_ms_recovery_operation as ctl
ctl.FOLDER=ROOT/'.work/alpha-t-onboarding-completion-20261009'
def main():
    label='supplemental-inputs';action=sys.argv[1]
    if action=='start':ctl.start(label,'ibkrv2stg-onboard-render','capture_t_completion_inputs_20261009.py',{'IBKR_RUN_MIGRATIONS':'false'})
    elif action=='status':ctl.read(label)
    elif action=='download':
        from azure.identity import AzureCliCredential
        from azure.storage.blob import BlobServiceClient
        proof=json.loads((ctl.FOLDER/(label+'-result.json')).read_bytes())
        if proof['status']!='Succeeded' or not proof['job_settings_unchanged']:raise ValueError('Execution not verified')
        r=next(r for r in proof['records'] if r.get('status')=='captured')
        data=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()).get_blob_client('internal-reports',r['blob']).download_blob().readall()
        if hashlib.sha256(data).hexdigest()!=r['sha256']:raise ValueError('Evidence hash mismatch')
        with (ctl.FOLDER/'supplemental-inputs.json').open('xb') as f:f.write(data)
        print(json.dumps({'downloaded':True,'sha256':r['sha256']}))
    else:raise ValueError('Unknown action')
if __name__=='__main__':main()
