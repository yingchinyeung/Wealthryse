"""Read-only MS release-blocker/source evidence capture. No market-data fetches."""
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path[:0] = ['/app/src', '/app/scripts']
import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from ibkr_etl.crisis_replay import resolve_holdings_as_of
from ibkr_etl.db import PostgresConnection

TENANT = '00000000-0000-0000-0000-000000000001'
SUBJECTS = ('alpha-world-ms', 'hong-kong-china', 'U16088571')
PREFIX = 'review/alpha-world-ms/crisis-release-20261008/preflight-v2/'


def main():
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row, connect_timeout=25,
                        options='-c default_transaction_read_only=on -c statement_timeout=120000') as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        raw.execute("SELECT set_config('app.tenant_id',%s,true)", (TENANT,))
        c = PostgresConnection(raw)
        snapshots = list(raw.execute("SELECT snapshot_id,period_end,data_as_of,data_sha256,quality_status,publication_blockers_json,snapshot_payload_json FROM reporting_snapshots WHERE tenant_id=%s AND subject_id='alpha-world-ms' AND status IN ('frozen','approved') ORDER BY period_end", (TENANT,)))
        if len(snapshots) != 21:
            raise ValueError('Exactly 21 active MS snapshots expected')
        resolved = []
        for row in snapshots:
            for subject in SUBJECTS:
                try:
                    day = resolve_holdings_as_of(c, account_id=subject, as_of=str(row['data_as_of']))
                    resolved.append({'period_end': str(row['period_end']), 'subject_id': subject, 'holdings_as_of': day})
                except RuntimeError:
                    resolved.append({'period_end': str(row['period_end']), 'subject_id': subject, 'holdings_as_of': None})
        days = sorted({str(r['holdings_as_of']) for r in resolved if r['holdings_as_of']})
        query_args = (TENANT, list(SUBJECTS), days)
        result = {'snapshots': snapshots, 'resolved': resolved,
            'replays': list(raw.execute('SELECT * FROM crisis_replay_daily WHERE tenant_id=%s AND subject_id=ANY(%s) AND holdings_as_of=ANY(%s) ORDER BY subject_id,holdings_as_of,observation_date', query_args)),
            'mappings': list(raw.execute('SELECT * FROM crisis_replay_components WHERE tenant_id=%s AND subject_id=ANY(%s) AND holdings_as_of=ANY(%s) ORDER BY subject_id,holdings_as_of,conid', query_args)),
            'source_nav': list(raw.execute('SELECT subject_id,snapshot_date,currency,total_text FROM subject_nav_series WHERE tenant_id=%s AND subject_id=ANY(%s) AND snapshot_date=ANY(%s) ORDER BY subject_id,snapshot_date', query_args)),
            'source_positions': list(raw.execute('SELECT subject_id,snapshot_date,conid,currency,market_value_text FROM subject_position_series WHERE tenant_id=%s AND subject_id=ANY(%s) AND snapshot_date=ANY(%s) ORDER BY subject_id,snapshot_date,conid', query_args)),
            'prices': list(raw.execute("SELECT * FROM market_data_prices WHERE tenant_id=%s AND instrument_id LIKE 'crisis-replay-%%' AND price_date BETWEEN '2008-09-05' AND '2009-03-09' ORDER BY symbol,price_date", (TENANT,))),
            'fx': list(raw.execute("SELECT * FROM reference_fx_rates WHERE tenant_id=%s AND ((from_currency='USD' AND to_currency='HKD') OR (from_currency='HKD' AND to_currency='USD')) AND rate_date BETWEEN '2008-09-05' AND '2009-03-09' ORDER BY rate_date,provider", (TENANT,))),
            'source_fx': list(raw.execute("SELECT * FROM reference_fx_rates WHERE tenant_id=%s AND from_currency='HKD' AND to_currency='USD' AND rate_date BETWEEN '2025-01-01' AND '2026-09-30' ORDER BY rate_date,provider", (TENANT,))),
            'ms_components': list(raw.execute("SELECT * FROM synthetic_portfolio_components WHERE tenant_id=%s AND portfolio_id='alpha-world-ms' AND snapshot_date=ANY(%s) ORDER BY snapshot_date,component_id", (TENANT, days))),
            'releases': list(raw.execute("SELECT r.* FROM report_artifact_releases r JOIN report_generation_runs g USING(tenant_id,generation_run_id) JOIN reporting_snapshots s USING(tenant_id,snapshot_id) WHERE s.tenant_id=%s AND s.subject_id='alpha-world-ms'", (TENANT,))),
            'database_writes': 0, 'market_data_requests': 0, 'read_only_enforced': True,
            'installed_source_sha256': {name: hashlib.sha256(Path('/app/src/ibkr_etl',name+'.py').read_bytes()).hexdigest() for name in ('risk_snapshots','alpha_world_ms_cache','internal_release','reporting_snapshot','alpha_world_ms_presentation','crisis_replay')}}
        raw.rollback()
    data = json.dumps(result, sort_keys=True, default=str, allow_nan=False).encode()
    client = BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'], credential=DefaultAzureCredential(exclude_interactive_browser_credential=True)).get_blob_client('internal-reports', PREFIX+'inputs.json')
    client.upload_blob(data, overwrite=False, content_settings=ContentSettings(content_type='application/json'), metadata={'sha256':hashlib.sha256(data).hexdigest()})
    if client.download_blob().readall() != data:
        raise ValueError('Private diagnostic readback differs')
    print(json.dumps({'status':'captured','months':21,'database_writes':0,'blob':PREFIX+'inputs.json','sha256':hashlib.sha256(data).hexdigest()}), flush=True)


if __name__ == '__main__':
    main()
