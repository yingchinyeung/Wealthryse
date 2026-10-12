"""One-shot read-only database audit with unchanged persistent Azure settings."""
import sys,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'.work/alpha-ms-azure-controller-runtime')]
import run_alpha_ms_recovery_operation as controller
controller.FOLDER=ROOT/'.work/alpha-world-t-accuracy-audit-20261009'

def main():
    controller.FOLDER.mkdir(exist_ok=True)
    mode=sys.argv[1]
    label='source-provenance' if len(sys.argv)>2 and sys.argv[2]=='provenance' else 'database-audit'
    script='audit_alpha_world_t_provenance_20261009.py' if label=='source-provenance' else 'audit_alpha_world_t_database_20261009.py'
    if mode=='start':controller.start(label,'ibkrv2stg-onboard-render',script,{'IBKR_RUN_MIGRATIONS':'false'})
    elif mode=='status':controller.read(label)
    elif mode=='download':
        from azure.identity import AzureCliCredential
        from azure.storage.blob import BlobServiceClient
        proof=json.loads((controller.FOLDER/(label+'-result.json')).read_bytes())
        assert proof['status']=='Succeeded' and proof['job_settings_unchanged']
        row=next(r for r in proof['records'] if r.get('status')=='inspected')
        service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential())
        raw=service.get_blob_client('internal-reports',row['blob']).download_blob().readall()
        assert hashlib.sha256(raw).hexdigest()==row['sha256']
        filename='provenance.json' if label=='source-provenance' else 'live-inputs.json'
        with (controller.FOLDER/filename).open('xb') as f:f.write(raw)
        print(json.dumps({'download_verified':True,'sha256':row['sha256'],'bytes':len(raw)}))
    else:raise ValueError('Unknown audit mode')

if __name__=='__main__':main()
