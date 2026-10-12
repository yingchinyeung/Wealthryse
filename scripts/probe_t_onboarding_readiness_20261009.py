"""Tenant-scoped, transaction-enforced read-only onboarding readiness probe."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

TENANT = '00000000-0000-0000-0000-000000000001'
SUBJECT = 'alpha-world-t'


def main():
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row,
                          connect_timeout=25,
                          options='-c default_transaction_read_only=on -c statement_timeout=30000') as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute("SELECT set_config('app.tenant_id',%s,true)", (TENANT,))
        result = {'captured_at': datetime.now(timezone.utc).isoformat()}
        result['subjects'] = list(c.execute('''SELECT subject_id,lifecycle_status,
            base_currency,reporting_currency,history_start_date FROM platform_subjects
            WHERE tenant_id=%s AND subject_id IN (%s,'alpha-world-ms','U16088571','U16088606')
            ORDER BY subject_id''', (TENANT, SUBJECT)))
        result['cases'] = list(c.execute('''SELECT case_id,stage,status,reporting_currency,
            requested_history_start FROM account_onboarding_cases
            WHERE tenant_id=%s AND target_subject_id=%s''', (TENANT, SUBJECT)))
        result['checks'] = list(c.execute('''SELECT k.check_id,k.status,k.message
            FROM account_onboarding_checks k JOIN account_onboarding_cases c
            ON k.tenant_id=c.tenant_id AND k.case_id=c.case_id
            WHERE c.tenant_id=%s AND c.target_subject_id=%s ORDER BY k.check_id''', (TENANT, SUBJECT)))
        result['snapshots'] = list(c.execute('''SELECT status,quality_status,COUNT(*) AS count
            FROM reporting_snapshots WHERE tenant_id=%s AND subject_id=%s
            GROUP BY status,quality_status''', (TENANT, SUBJECT)))
        result['reports'] = list(c.execute('''SELECT r.report_type,r.status,COUNT(*) AS count
            FROM report_generation_runs r JOIN reporting_snapshots s
            ON r.tenant_id=s.tenant_id AND r.snapshot_id=s.snapshot_id
            WHERE s.tenant_id=%s AND s.subject_id=%s GROUP BY r.report_type,r.status''', (TENANT, SUBJECT)))
        result['risk_cache'] = list(c.execute('''SELECT quality_status,COUNT(*) AS count
            FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id=%s
            GROUP BY quality_status''', (TENANT, SUBJECT)))
        result['read_only'] = c.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] == 'on'
        assert result['read_only']
        c.rollback()
    result['runtime_sha256'] = {}
    for name in ('risk_snapshots.py', 'alpha_world_t_analytics.py', 'alpha_world_t_cache.py',
                 'alpha_world_t_crisis_policy.py', 'reporting_snapshot.py'):
        path = Path('/app/src/ibkr_etl') / name
        result['runtime_sha256'][name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    result['production_data_written'] = False
    print('ALPHA_MS_RECOVERY_RESULT ' + json.dumps(result, default=str), flush=True)


if __name__ == '__main__':
    main()
