"""Independent Azure absence check; never claims email or complete capture."""
from .agent_report_notifications import ready_query, rule_definition

RULE = 'ibkr-v2-specialist-daily-report-missing'
FIRST_PERIOD_END = '2026-10-03T10:00:00Z'
GRACE_MINUTES = 15


def missing_query():
    # Always creates an expected row even when no job/log exists. This detects
    # a never-started worker as well as a succeeded worker without useful work.
    return '\n'.join([
        'let ExpectedEnd = iff(now() >= startofday(now()) + 10h, startofday(now()) + 10h, startofday(now()) - 1d + 10h);',
        'let ExpectedDate = format_datetime(ExpectedEnd, "yyyy-MM-dd");',
        'let ValidPublication = (' + ready_query() + '\n| where ReportDate == ExpectedDate);',
        'print ReportDate=ExpectedDate, ExpectedEnd=ExpectedEnd',
        f'| where ExpectedEnd >= datetime({FIRST_PERIOD_END}) and now() >= ExpectedEnd + {GRACE_MINUTES}m',
        '| extend ValidatedPublications=toscalar(ValidPublication | count)',
        '| where ValidatedPublications == 0',
        '| project ReportDate, State="validated_publication_not_observed"',
    ])


def watchdog_definition(*, workspace_id, action_group_id, enabled=False):
    if type(enabled) is not bool:
        raise ValueError('Exact watchdog activation flag required')
    value = rule_definition(workspace_id=workspace_id, action_group_id=action_group_id)
    value['tags'].update(purpose='specialist-report-missed-publication', mode='active' if enabled else 'dormant')
    p = value['properties']
    p.update(displayName='Agent daily report publication needs attention',
        description='No validated current daily-report publication was observed after the 18:00 Hong Kong cutoff and 15-minute grace. This is not proof of source-data loss or recipient email delivery.',
        enabled=enabled, severity=2)
    p['criteria']['allOf'][0].update(query=missing_query(),
        dimensions=[dict(name='ReportDate', operator='Include', values=['*'])])
    p['actions']['customProperties'].update(Purpose='Daily publication missing or unverified after grace',
        Timing='18:00 Asia/Hong_Kong plus 15-minute grace',
        Check='Independent Azure telemetry check; inspect private inbox and worker evidence')
    return value
