"""Read-only exact-state evidence for the owner-requested HKD correction."""
import hashlib
import json
import os
from datetime import datetime, timezone

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
        queries = {
            'platform_subjects': 'subject_id=%s',
            'derived_portfolios': 'portfolio_id=%s',
            'subject_calculation_definitions': 'subject_id=%s',
            'account_onboarding_cases': 'target_subject_id=%s',
            'account_onboarding_approvals': 'subject_id=%s',
            'portfolio_benchmark_mapping': 'subject_id=%s',
            'subject_feature_states': 'subject_id=%s',
            'subject_dependencies': 'subject_id=%s',
            'account_onboarding_checks': 'case_id IN (SELECT case_id FROM account_onboarding_cases WHERE tenant_id=app_tenant_id() AND target_subject_id=%s)',
        }
        tables = {table: list(c.execute('SELECT * FROM '+table+' WHERE tenant_id=%s AND '+where,
                                        (TENANT, SUBJECT))) for table, where in queries.items()}
        # Counts establish that no calculated/released artifacts will be orphaned.
        counts = {table: c.execute('SELECT count(*) AS n FROM '+table+' WHERE tenant_id=%s AND '+field+'=%s',
                                  (TENANT, SUBJECT)).fetchone()['n']
                  for table, field in (('derived_portfolio_nav','portfolio_id'),
                                      ('risk_analytics_snapshots','subject_id'),
                                      ('reporting_snapshots','subject_id'))}
        if c.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] != 'on':
            raise ValueError('Read-only guard absent')
        c.rollback()
    serialized = json.dumps(tables, default=str, sort_keys=True, separators=(',',':'))
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({
        'captured_at': datetime.now(timezone.utc).isoformat(), 'tables':tables,
        'tables_sha256':hashlib.sha256(serialized.encode()).hexdigest(),
        'artifact_counts':counts, 'database_persistent_writes':0,
    }, default=str), flush=True)


if __name__ == '__main__':
    main()
