"""Read-only identity and operating-date diagnosis; no market fetches."""
import os,sys,json
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl import web
with web._database() as db:
    if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
    mappings=[dict(r) for r in db.execute("SELECT subject_id,holdings_as_of,conid,holding_symbol,source_symbol,mapping_method,proxy_reason FROM crisis_replay_components WHERE tenant_id=%s AND conid='804144296' AND holdings_as_of>='2026-10-01' ORDER BY subject_id,holdings_as_of",(web.TENANT_ID,)).fetchall()]
    holdings=[dict(r) for r in db.execute("SELECT subject_id,snapshot_date,conid,currency FROM subject_position_series WHERE tenant_id=%s AND conid='804144296' AND snapshot_date>='2026-10-07' ORDER BY subject_id,snapshot_date",(web.TENANT_ID,)).fetchall()]
    registrations=[dict(r) for r in db.execute("SELECT subject_id,capability,operating_state,to_jsonb(r)->>'created_at' AS created_at,to_jsonb(r)->>'updated_at' AS updated_at FROM subject_operating_registrations r WHERE tenant_id=%s AND subject_id='alpha-world-t' AND capability IN ('benchmark_refresh','risk_snapshot','daily_sync')",(web.TENANT_ID,)).fetchall()]
print('PLATFORM_CORRECTION_PROBE '+json.dumps({'read_only':True,'database_writes':0,'mappings':mappings,'current_holdings':holdings,'t_registrations':registrations},default=str),flush=True)
