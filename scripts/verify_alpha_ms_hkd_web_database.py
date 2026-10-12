"""Read-only probe run inside the already-installed website container."""
import hashlib
import json
import os
from pathlib import Path
import psycopg
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
from ibkr_etl.subject_read_contract import require_ms_hkd_contract
from ibkr_etl.alpha_world_ms_hkd import load_risk_snapshot
from ibkr_etl.risk_snapshots import snapshot_read_status

TENANT='00000000-0000-0000-0000-000000000001'
release=json.loads(Path('/app/ALPHA_MS_HKD_RELEASE.json').read_bytes())
for name,digest in release['installed_files_sha256'].items():
    if hashlib.sha256(Path('/app',name).read_bytes()).hexdigest()!=digest:raise ValueError('Actual website source differs')
with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,connect_timeout=20,
        options='-c default_transaction_read_only=on -c statement_timeout=30000') as raw:
    raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    raw.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,));c=PostgresConnection(raw)
    role=dict(raw.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone())
    if role['rolsuper'] or role['rolbypassrls']:raise ValueError('Non-admin actual website role required')
    require_ms_hkd_contract(c,tenant_id=TENANT)
    _,metrics,evidence=load_risk_snapshot(c,'2026-09-30',tenant_id=TENANT)
    row=dict(c.execute("SELECT * FROM risk_analytics_snapshots WHERE tenant_id=? AND subject_id='alpha-world-ms' AND as_of_date='2026-09-30' ORDER BY generated_at DESC LIMIT 1",(TENANT,)).fetchone())
    if snapshot_read_status(row,subject_id='alpha-world-ms',as_of='2026-09-30')!='current':raise ValueError('Actual website cache read failed')
    raw.execute("SELECT set_config('app.tenant_id',%s,true)",('11111111-1111-1111-1111-111111111111',))
    if raw.execute("SELECT count(*) FROM platform_subjects WHERE tenant_id=%s AND subject_id='alpha-world-ms'",(TENANT,)).fetchone()[0]:raise ValueError('Actual website tenant isolation failed')
    raw.rollback()
print('ALPHA_MS_WEB_DATABASE_VERIFIED '+json.dumps({'status':'passed','role':role,'installed_source_files_verified':True,
    'current_currency':'HKD','contract_sha256':release['contract_sha256'],'actual_website_cache_read':'current',
    'tenant_isolation':'passed','metrics_sha256':hashlib.sha256(json.dumps(metrics,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
    'database_writes':0}),flush=True)
