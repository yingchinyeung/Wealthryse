"""Read-only, tenant-scoped evidence after governed website actions."""
import json
import os
from datetime import datetime, timezone
import psycopg
from psycopg.rows import dict_row

TENANT = '00000000-0000-0000-0000-000000000001'


def main():
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row,
                        connect_timeout=25, options='-c default_transaction_read_only=on -c statement_timeout=30000') as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute("SELECT set_config('app.tenant_id',%s,true)", (TENANT,))
        def rows(sql):
            return list(c.execute(sql, (TENANT,)))
        result = {'captured_at':datetime.now(timezone.utc).isoformat(),
                  'persistent_writes':0}
        result['subjects'] = rows("""SELECT subject_id,lifecycle_status,base_currency,reporting_currency
            FROM platform_subjects WHERE tenant_id=%s AND subject_id IN
            ('alpha-world-t','alpha-world-ms','hong-kong-china','U16088571','U16088606') ORDER BY subject_id""")
        result['definition'] = rows("""SELECT definition_id,definition_version,configuration_sha256,approval_status
            FROM subject_calculation_definitions WHERE tenant_id=%s AND subject_id='alpha-world-t' AND effective_to IS NULL""")
        result['approvals'] = rows("""SELECT approval_id,approval_type,approval_status,scope_sha256,approved_at
            FROM account_onboarding_approvals WHERE tenant_id=%s AND subject_id='alpha-world-t'
            AND revoked_at IS NULL ORDER BY approval_type""")
        result['benchmarks'] = rows("""SELECT benchmark_id,mapping_type,reporting_currency,effective_from
            FROM portfolio_benchmark_mapping WHERE tenant_id=%s AND subject_id='alpha-world-t'
            AND effective_to IS NULL ORDER BY mapping_type,benchmark_id""")
        result['checks'] = rows("""SELECT k.check_id,k.status,k.message,k.evidence_json
            FROM account_onboarding_checks k JOIN account_onboarding_cases c ON k.tenant_id=c.tenant_id AND k.case_id=c.case_id
            WHERE c.tenant_id=%s AND c.target_subject_id='alpha-world-t' ORDER BY k.check_id""")
        result['nav'] = rows("""SELECT currency,MIN(snapshot_date) AS first_date,MAX(snapshot_date) AS latest_date,COUNT(*) AS observations
            FROM subject_nav_series WHERE tenant_id=%s AND subject_id='alpha-world-t' GROUP BY currency""")
        result['risk'] = rows("""SELECT quality_status,COUNT(*) AS observations FROM risk_analytics_snapshots
            WHERE tenant_id=%s AND subject_id='alpha-world-t' GROUP BY quality_status""")
        result['read_only'] = c.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']=='on'
        assert result['read_only']
        c.rollback()
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps(result,default=str),flush=True)


if __name__=='__main__':
    main()
