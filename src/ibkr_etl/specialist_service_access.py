"""Narrow capability roles for trusted audit/report services, never agent logins.

These roles do not authorize any source table access, source mutation, schema
administration, role membership changes, or report approval/release actions.
"""
ROLES = {
    'audit': 'ibkr_specialist_audit_writer',
    'report': 'ibkr_specialist_report_worker',
    'reader': 'ibkr_specialist_report_reader',
}
TABLES = {'specialist_audit_events','specialist_daily_reports','specialist_report_deliveries'}
STATUS_COLUMNS = 'state,lease_token,lease_expires_at,attempts,updated_at,last_error_code,provider_reference'


def grant_statements(capability, *, schema='public'):
    if schema not in {'public','specialist_report_builder_trial'} or capability not in ROLES:
        raise ValueError('Unregistered specialist capability or schema')
    role=ROLES[capability]+('_trial' if schema=='specialist_report_builder_trial' else '')
    grants=[f'GRANT USAGE ON SCHEMA {schema} TO {role}',
            f'GRANT EXECUTE ON FUNCTION {schema}.app_tenant_id() TO {role}']
    if capability=='audit':
        grants.append(f'GRANT SELECT,INSERT ON {schema}.specialist_audit_events TO {role}')
    elif capability=='report':
        grants.extend([f'GRANT SELECT ON {schema}.specialist_audit_events TO {role}',
            f'GRANT SELECT,INSERT ON {schema}.specialist_daily_reports,{schema}.specialist_report_deliveries TO {role}',
            f'GRANT UPDATE ({STATUS_COLUMNS}) ON {schema}.specialist_report_deliveries TO {role}'])
    else:
        grants.append(f'GRANT SELECT ON {schema}.specialist_daily_reports,{schema}.specialist_report_deliveries TO {role}')
    return grants
