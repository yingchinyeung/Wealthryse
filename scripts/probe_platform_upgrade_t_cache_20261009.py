"""Read-only cache mismatch classification. Never emit financial values or secrets."""
import os, sys, json
os.environ['PGOPTIONS'] = '-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0] = ['/app/src', '/app/scripts']
from ibkr_etl import web
from ibkr_etl.alpha_world_t_analytics import load_risk_snapshot
from ibkr_etl.alpha_world_t_crisis import load as load_crisis
from ibkr_etl.risk_snapshots import snapshot_read_status, decoded_payload, RISK_ANALYTICS_VERSION
from ibkr_etl.util import canonical_json


def differences(a, b, path=''):
    if canonical_json(a) == canonical_json(b):
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        result = []
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                result.append(path + '/' + key + ':missing-key')
            else:
                result += differences(a[key], b[key], path + '/' + key)
        return result[:50]
    return [path + ':different']


with web._database() as raw:
    assert raw.execute("SHOW transaction_read_only").fetchone()['transaction_read_only'] == 'on'
    day = str(raw.execute("SELECT MAX(snapshot_date) AS day FROM subject_nav_series WHERE tenant_id=%s AND subject_id='alpha-world-t' AND currency='HKD'", (web.TENANT_ID,)).fetchone()['day'])
    row = raw.execute("SELECT payload_json,payload_sha256,quality_status,comparison_version FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id='alpha-world-t' AND as_of_date=%s AND analytics_version=%s", (web.TENANT_ID, day, RISK_ANALYTICS_VERSION)).fetchone()
    status = snapshot_read_status(row, subject_id='alpha-world-t', as_of=day)
    payload = decoded_payload(row['payload_json'])
    connection = web._portable_database_connection(raw)
    source, metrics, evidence = load_risk_snapshot(connection, day, tenant_id=web.TENANT_ID)
    try:
        crisis = load_crisis(connection, source, tenant_id=web.TENANT_ID)
        crisis_error = None
    except ValueError as error:
        crisis = None
        crisis_error = str(error)
    record = {'read_only': True, 'database_writes': 0, 'as_of': day, 'cache_status': status,
              'source_differences': differences(payload.get('source_snapshot'), source),
              'core_difference_keys': [k for k, v in metrics.items() if payload['metrics'].get(k) != v],
              'crisis_differences': differences(payload.get('crisis_replay'), crisis),
              'crisis_error': crisis_error}
print('PLATFORM_CORRECTION_PROBE ' + json.dumps(record), flush=True)
