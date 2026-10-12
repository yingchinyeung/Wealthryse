"""Qualify a T cache refresh without storing it. Report hashes and changed keys only."""
import os, sys, json, hashlib
os.environ['PGOPTIONS'] = '-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0] = ['/app/src', '/app/scripts']
from ibkr_etl import web
from ibkr_etl.risk_snapshots import compute_risk_snapshot, decoded_payload, snapshot_read_status, RISK_ANALYTICS_VERSION
from ibkr_etl.util import canonical_json


def sha(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


with web._database() as raw:
    assert raw.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] == 'on'
    day = str(raw.execute("SELECT MAX(snapshot_date) AS day FROM subject_nav_series WHERE tenant_id=%s AND subject_id='alpha-world-t' AND currency='HKD'", (web.TENANT_ID,)).fetchone()['day'])
    row = raw.execute("SELECT * FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id='alpha-world-t' AND as_of_date=%s AND analytics_version=%s", (web.TENANT_ID, day, RISK_ANALYTICS_VERSION)).fetchone()
    old = decoded_payload(row['payload_json'])
    fresh, timings, source_sha = compute_risk_snapshot(web._portable_database_connection(raw), tenant_id=web.TENANT_ID, subject_id='alpha-world-t', as_of=day)
    different = [k for k in sorted(set(old) | set(fresh)) if canonical_json(old.get(k)) != canonical_json(fresh.get(k))]
    crisis_old, crisis_new = old['crisis_replay'], fresh['crisis_replay']
    crisis_keys = [k for k in sorted(set(crisis_old) | set(crisis_new)) if canonical_json(crisis_old.get(k)) != canonical_json(crisis_new.get(k))]
    records = []
    for name in sorted(set(crisis_old['inputs']['records']) | set(crisis_new['inputs']['records'])):
        a, b = crisis_old['inputs']['records'].get(name), crisis_new['inputs']['records'].get(name)
        if canonical_json(a) == canonical_json(b):
            continue
        keys = set()
        if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for before, after in zip(a, b):
                keys.update(k for k in set(before) | set(after) if canonical_json(before.get(k)) != canonical_json(after.get(k)))
        else:
            keys.add('record_count_or_type_changed')
        records.append({'record_set': name, 'changed_columns': sorted(keys)})
    status = snapshot_read_status({'payload_json': fresh, 'payload_sha256': sha(fresh), 'quality_status': fresh['quality_status'], 'comparison_version': row['comparison_version']}, subject_id='alpha-world-t', as_of=day)
    result = {'read_only': True, 'database_writes': 0, 'subject': 'alpha-world-t', 'as_of': day,
              'old_payload_sha256': row['payload_sha256'], 'new_payload_sha256': sha(fresh),
              'new_source_sha256': source_sha, 'fresh_validation': status,
              'all_metrics_identical': canonical_json(old['metrics']) == canonical_json(fresh['metrics']),
              'changed_payload_keys': different, 'changed_crisis_keys': crisis_keys,
              'changed_crisis_record_columns': records,
              'refresh_qualified': status == 'current' and different == ['crisis_replay']
                 and crisis_keys == ['input_sha256', 'inputs']}
print('PLATFORM_CORRECTION_PROBE ' + json.dumps(result), flush=True)
