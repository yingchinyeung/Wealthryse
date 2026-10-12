"""Refresh exactly one qualified disposable cache; no source/report/approval writes.

REFRESH_PLAN is injected by the controller from the successful read-only rehearsal.
"""
import os, sys, json, hashlib
sys.path[:0] = ['/app/src', '/app/scripts']
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.db import PostgresConnection
from ibkr_etl.risk_snapshots import compute_risk_snapshot, decoded_payload, snapshot_read_status, RISK_ANALYTICS_VERSION
from ibkr_etl.util import canonical_json, utc_now_iso
from ibkr_etl.qualified_agent_risk import t_agent_payload


def sha(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def main():
    plan = REFRESH_PLAN
    tenant = os.environ['PORTFOLIO_TENANT_ID']
    subject = 'alpha-world-t'
    day = plan['as_of'][:10]
    assert tenant == '00000000-0000-0000-0000-000000000001'
    assert subject == plan['subject'] and day == '2026-10-07'
    scope = (tenant, subject, day, RISK_ANALYTICS_VERSION)
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row, connect_timeout=10,
                         options='-c statement_timeout=120000 -c lock_timeout=5000') as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ WRITE')
        raw.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
        key = int.from_bytes(hashlib.sha256(canonical_json(['risk-refresh-v1',tenant,subject,day]).encode()).digest()[:8], byteorder='big', signed=True)
        assert raw.execute('SELECT pg_try_advisory_xact_lock(%s) AS acquired', (key,)).fetchone()['acquired'], 'Risk refresh already running'
        row = raw.execute('SELECT * FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s FOR UPDATE', scope).fetchone()
        if not row or row['payload_sha256'] != plan['old_payload_sha256']:
            raise ValueError('Cache changed since rehearsal; inspect rather than overwrite')
        latest = str(raw.execute("SELECT MAX(snapshot_date) AS day FROM subject_nav_series WHERE tenant_id=%s AND subject_id=%s AND currency='HKD'", (tenant,subject)).fetchone()['day'])
        if latest != day:
            raise ValueError('Latest NAV changed; requalify')
        old = decoded_payload(row['payload_json'])
        payload, timings, source_sha = compute_risk_snapshot(PostgresConnection(raw), tenant_id=tenant, subject_id=subject, as_of=day)
        if (sha(payload) != plan['new_payload_sha256'] or source_sha != plan['new_source_sha256']
                or canonical_json(payload['metrics']) != canonical_json(old['metrics'])):
            raise ValueError('Rehearsed output or unchanged metrics differ')
        fresh = {'payload_json': payload, 'payload_sha256': sha(payload),
                 'quality_status': payload['quality_status'], 'comparison_version': row['comparison_version']}
        if snapshot_read_status(fresh, subject_id=subject, as_of=day) != 'current':
            raise ValueError('Fresh cache validation failed')
        changed = raw.execute('''UPDATE risk_analytics_snapshots SET source_data_sha256=%s,
          payload_sha256=%s,payload_json=%s::jsonb,generated_at=%s,duration_ms=%s,timings_json=%s::jsonb
          WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s AND payload_sha256=%s''',
          (source_sha, sha(payload), canonical_json(payload), utc_now_iso(), float(timings['total_ms']),
           canonical_json({**timings,'source_check_status':'current','source_checked_at':utc_now_iso(),
                           'refresh_reason':'qualified-provenance-only-after-sync'}), *scope, plan['old_payload_sha256'])).rowcount
        if changed != 1:
            raise ValueError('Expected exactly one cache update')
        verified = t_agent_payload(raw, tenant_id=tenant, as_of=day, source_snapshot=payload['source_snapshot'],
                                   core_metrics=payload['metrics'], crisis=payload['crisis_replay'])
        assert sha(verified) == plan['new_payload_sha256']
    print('PLATFORM_CORRECTION_PROBE ' + json.dumps({'status':'passed','subject':subject,'as_of':day,
          'cache_rows_updated':1,'all_metrics_unchanged':True,'source_data_changed':False,
          'reports_changed':False,'approvals_changed':False,'new_payload_sha256':plan['new_payload_sha256']}), flush=True)


if __name__ == '__main__':
    main()
