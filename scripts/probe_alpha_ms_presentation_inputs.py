"""Export existing frozen MS inputs privately, with no database/source writes."""
import hashlib
import json
import os
import sys
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl.subject_read_contract import require_ms_hkd_contract
from ibkr_etl.alpha_world_ms_analytics import analyze

def main():
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    prefix=os.environ['MS_PRESENTATION_REVIEW_PREFIX'];tenant='00000000-0000-0000-0000-000000000001'
    items=[]
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,connect_timeout=25,
            options='-c default_transaction_read_only=on -c statement_timeout=120000') as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        raw.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
        c=PostgresConnection(raw);require_ms_hkd_contract(c,tenant_id=tenant)
        rows=list(c.execute("SELECT snapshot_id,period_end,data_sha256,quality_status,publication_allowed,snapshot_payload_json FROM reporting_snapshots WHERE subject_id=? AND status IN ('frozen','approved') ORDER BY period_end",('alpha-world-ms',)))
        if len(rows)!=21 or len({str(r['period_end']) for r in rows})!=21:raise ValueError('Expected 21 unique frozen months')
        for row in rows:
            data=str(row['snapshot_payload_json']).encode();payload=json.loads(data)
            if hashlib.sha256(data).hexdigest()!=row['data_sha256']:raise ValueError('Frozen hash differs')
            if payload['reporting_currency']!='HKD' or row['quality_status']=='fail' or row['publication_allowed']:raise ValueError('Private HKD boundary differs')
            metrics,_,evidence=analyze(payload)
            if payload['risk']['metrics']!=metrics:raise ValueError('Stored metrics differ from recomputation')
            blob_name=prefix+'inputs/'+str(row['period_end'])+'.json'
            blob=service.get_blob_client('internal-reports',blob_name)
            blob.upload_blob(data,overwrite=False,content_settings=ContentSettings(content_type='application/json'))
            if blob.download_blob().readall()!=data:raise ValueError('Private input readback differs')
            items.append({'period_end':str(row['period_end']),'snapshot_id':str(row['snapshot_id']),
                'sha256':row['data_sha256'],'blob_name':blob_name,'calculation_version':evidence['version']})
        raw.rollback()
    proof={'status':'passed','items':items,'database_writes':0,'ibkr_source_changed':False,'reports_released':False,'onboarding_activated':False}
    data=json.dumps(proof,sort_keys=True,indent=2).encode()
    blob=service.get_blob_client('internal-reports',prefix+'input-proof.json')
    blob.upload_blob(data,overwrite=False,content_settings=ContentSettings(content_type='application/json'))
    if blob.download_blob().readall()!=data:raise ValueError('Proof readback differs')
    print('MS_PRESENTATION_INPUTS '+json.dumps({'status':'passed','months':len(items)}),flush=True)

if __name__=='__main__':main()
