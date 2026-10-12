"""Read-only exact source/cache replay in the worker's native image."""
import os,sys,json,hashlib
from pathlib import Path
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl.db import connect
from ibkr_etl.risk_snapshots import compute_risk_snapshot,decoded_payload,snapshot_read_status
from ibkr_etl.util import canonical_json
tenant=os.environ['PORTFOLIO_TENANT_ID'];day=EXPECTED['as_of'][:10]
connection=connect(Path('/tmp/unused.sqlite'),os.environ['DATABASE_URL'],tenant)
try:
    if connection.execute("SHOW transaction_read_only").fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
    old=connection.execute("SELECT * FROM risk_analytics_snapshots WHERE tenant_id=? AND subject_id='alpha-world-ms' AND as_of_date=? AND analytics_version='risk-analytics-v2'",(tenant,day)).fetchone()
    if old['payload_sha256']!=EXPECTED['existing_payload_sha256']:raise ValueError('Cache drift')
    payload,_,source=compute_risk_snapshot(connection,tenant_id=tenant,subject_id='alpha-world-ms',as_of=day)
    digest=hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    expected=EXPECTED['existing_payload_sha256'] if FALLBACK else EXPECTED['candidate_payload_sha256']
    if digest!=expected:raise ValueError('Worker arithmetic differs from qualified output')
    if not FALLBACK and source!=EXPECTED['candidate_source_sha256']:raise ValueError('Worker input fingerprint differs')
    prior=decoded_payload(old['payload_json'])
    if any(payload['metrics'].get(k)!=v for k,v in prior['metrics'].items()):raise ValueError('Original core changed')
    fresh={**dict(old),'payload_json':payload,'payload_sha256':digest}
    if snapshot_read_status(fresh,subject_id='alpha-world-ms',as_of=day)!='current':raise ValueError('Cache readback contract failed')
    files=['risk_snapshots.py']+([] if FALLBACK else ['alpha_world_ms_runtime.py'])
    print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':True,'fallback':FALLBACK,'read_only':True,'database_writes':0,
        'as_of':day,'core_metrics_identical':True,'payload_sha256':digest,'source_sha256':source,
        'file_sha256':{n:hashlib.sha256(Path('/app/src/ibkr_etl',n).read_bytes()).hexdigest() for n in files}}),flush=True)
finally:connection.close()
