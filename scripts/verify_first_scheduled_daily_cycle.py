"""Accept exactly the first natural daily report, without starting any work.

`inspect` is read-only. `retain` adds one create-only protected acceptance and
its local reservation; it never dispatches a job, connects to PostgreSQL,
changes a schedule/grant, sends email or calls a model. An uncertain evidence
write leaves the reservation in place: reconcile it, never automatically retry.
Telemetry proves the worker's fresh-login readback, not an independent desktop
read of the private report, six-agent activation or recipient email delivery.
"""
import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from azure.core import MatchConditions
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient

from audit_azure_runtime import az
from deploy_metadata_monitor import BASE, SUB, request
from deploy_specialist_daily_schedule import FOLDER, IMAGE, WORKSPACE, bundle, validate
from deploy_specialist_report_worker import ROOT, JOB, job_path
from deploy_agent_report_notifications import GROUP_PATH, RULE_PATH, validate_rule
from enable_specialist_ready_notification import wanted as ready_definition
from deploy_specialist_report_watchdog import wanted as missing_definition
from run_specialist_gateway_read_verification import retain, verify_container
from verify_owner_task_source_checkpoint import source_manifest
from deploy_owner_task_postgres_trial import archive_protection
from ibkr_etl.agent_report_notifications import PREFIX, RULE, ready_query, serialize_ready_event
from ibkr_etl.agent_report_watchdog import RULE as MISSING_RULE, missing_query
from ibkr_etl.specialist_archive_reader import ACCOUNT, CONTAINER


EXECUTION = 'ibkrv2stg-agent-report-29850360'
REPORT_DATE = '2026-10-03'
SCHEDULED_AT = '2026-10-03T10:00:00+00:00'
SIGNED_ACCOUNT = 'anthony.ying@wealthryse.ai'
ENTRA_TENANT = '0578a50b-a7a1-40a4-905b-8b20dba84124'
PAYLOAD_SHA256 = 'd2a920dc5c14cb7f9d5fa97bf49d4513646c52fdbef80376819a3a5454ea8e34'
REPORT_SHA256 = 'cec90e711932419324d088e6e11a3cf8bc6a4dd93d72079ac380ec0bc05897ed'
NOTIFICATION_KEY = '2db447cf964d2cc0f007c864819027e2d065da36984622737c199264dee5dd21'
SOURCE_SHA256 = '565c1fac22a131203b3cd856a91e462a7821e2dc0cd9b24cd74304a6de0713f5'
SOURCE_VERSION = '2026-10-03T09:12:50.2999170Z'
SCHEDULE_INTENT_SHA256 = '7dfaa06ff8da3bf647d25eec1a9915ddf95665511f874b645630953c9675baa4'
SCHEDULE_INTENT_VERSION = '2026-10-03T02:01:59.3828186Z'
SCHEDULE_SHA256 = '666a6511de60e4f9fb92966768f409dd3b7cb7f086fb49dc475778fcbac2be49'
SCHEDULE_VERSION = '2026-10-03T02:03:20.9080012Z'
NATURAL_ALERT_ID = (BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs'
    '/providers/Microsoft.AlertsManagement/alerts/ebbf5f36-5972-202b-e11f-e44b5616001c')
ACCEPTANCE_KIND = 'first-natural-daily-cycle-20261003'
TARGET = FOLDER/'first-natural-cycle-acceptance.json'
CYCLE_PREFIX = 'SPECIALIST_DAILY_CYCLE '
COVERAGE = 'specialist-ledger-only; complete-system capture not established'
KINDS = {'daily-worker-schedule-intent', 'daily-worker-schedule-completed',
         'owner-task-pg-source-checkpoint', ACCEPTANCE_KIND}
MAX_BYTES = 2*1024*1024


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def equal(actual, expected, label):
    # JSON boolean/int aliases must not make false equal zero or true equal one.
    if type(actual) is not type(expected):
        raise ValueError(label+' type differs')
    if isinstance(expected, dict):
        if set(actual) != set(expected):
            raise ValueError(label+' fields differ')
        for key, value in expected.items():
            equal(actual[key], value, label+'.'+key)
    elif isinstance(expected, list):
        if len(actual) != len(expected):
            raise ValueError(label+' count differs')
        for index, value in enumerate(expected):
            equal(actual[index], value, label+'.'+str(index))
    elif actual != expected:
        raise ValueError(label+' differs')


def utc(value):
    if type(value) is not str:
        raise ValueError('Explicit UTC timestamp required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() != timedelta(0):
        raise ValueError('Explicit UTC timestamp required')
    return result


def validate_receipt(record, kind, *, expected_sha=None, expected_version=None):
    if kind not in KINDS or type(record) is not dict:
        raise ValueError('Registered protected acceptance required')
    receipt = record.get('protected_receipt')
    value = {k:v for k,v in record.items() if k != 'protected_receipt'}
    payload = canonical(value)
    sha = hashlib.sha256(payload).hexdigest()
    if (type(receipt) is not dict or set(receipt) !=
            {'blob','sha256','version_id','protected_readback_verified'}
            or receipt.get('protected_readback_verified') is not True
            or receipt.get('sha256') != sha or (expected_sha and sha != expected_sha)
            or receipt.get('blob') != f'acceptance/gateway-read-{kind}-{sha}.json'
            or type(receipt.get('version_id')) is not str
            or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z',receipt['version_id'])
            or (expected_version and receipt['version_id'] != expected_version)
            or not 0 < len(payload) <= MAX_BYTES):
        raise ValueError('Exact protected acceptance receipt differs')
    utc(receipt['version_id'])
    return value, payload


def read_protected(record, kind, *, expected_sha=None, expected_version=None):
    """Independently read the exact retained version under its observed ETag."""
    value, payload = validate_receipt(record, kind, expected_sha=expected_sha,
                                     expected_version=expected_version)
    receipt = record['protected_receipt']
    archive_protection()
    service = BlobServiceClient('https://'+ACCOUNT+'.blob.core.windows.net',
                               credential=AzureCliCredential())
    blob = service.get_blob_client(CONTAINER, receipt['blob'], version_id=receipt['version_id'])
    props = blob.get_blob_properties()
    if (type(props.size) is not int or props.size != len(payload)
            or props.version_id != receipt['version_id'] or not props.etag
            or blob.download_blob(offset=0, length=len(payload)+1, etag=props.etag,
                match_condition=MatchConditions.IfNotModified).readall() != payload):
        raise ValueError('Independent version-pinned protected readback differs')
    archive_protection()
    return value


def validate_source_checkpoint(value):
    expected = dict(status='validated-source-live-sql-pending', ibkr_source_writes=False,
        model_calls=0, owner_runtime_active=False, six_specialist_runtimes_active=False,
        production_migration_059_installed=False, natural_daily_cycle_verified=False)
    for key, wanted in expected.items():
        equal(value.get(key), wanted, 'historical source checkpoint.'+key)


def validate_action_group(group, expected_sha):
    properties = group.get('properties', {})
    if (type(group.get('id')) is not str or group['id'].casefold() != GROUP_PATH.casefold()
            or type(expected_sha) is not str or not re.fullmatch('[0-9a-f]{64}',expected_sha)
            or properties.get('enabled') is not True
            or not any(row.get('emailAddress') == SIGNED_ACCOUNT and row.get('status') == 'Enabled'
                       for row in properties.get('emailReceivers', []))
            or digest(properties) != expected_sha):
        raise ValueError('Exact protected enabled owner notification group differs')
    return expected_sha


def normalize_cli_log_rows(rows):
    """Remove only the measured, fixed Azure CLI result-table wrapper."""
    if type(rows) is not list or len(rows) != 2:
        raise ValueError('Exactly two scoped CLI telemetry rows required')
    normalized = []
    for row in rows:
        if (type(row) is not dict or set(row) != {'TimeGenerated','Log_s','TableName'}
                or any(type(row[key]) is not str for key in row)
                or row['TableName'] != 'PrimaryResult'):
            raise ValueError('Exact registered CLI telemetry wrapper required')
        normalized.append({'TimeGenerated':row['TimeGenerated'], 'Log_s':row['Log_s']})
    return normalized


def validate_cycle(run, log_rows, ready_rows, missing_rows, alerts, *, now):
    """Pure bounded metadata linkage, not independent SQL/report-content QA."""
    scheduled = utc(SCHEDULED_AT)
    if (not isinstance(now, datetime) or now.utcoffset() != timedelta(0)
            or not scheduled <= now < scheduled+timedelta(days=1)):
        raise ValueError('First report live-notification window has closed')
    if type(run) is not dict or run.get('name') != EXECUTION:
        raise ValueError('Exact first natural execution required')
    properties = run.get('properties', {})
    if properties.get('status') != 'Succeeded' or utc(properties.get('startTime')) != scheduled:
        raise ValueError('First natural execution did not succeed at its deadline')
    ended = utc(properties.get('endTime'))
    if not scheduled <= ended <= min(now, scheduled+timedelta(minutes=15)):
        raise ValueError('First natural execution end time differs')
    if type(log_rows) is not list or len(log_rows) != 2:
        raise ValueError('Exactly one cycle and one readiness event required')
    cycles, notices = [], []
    for row in log_rows:
        if type(row) is not dict or set(row) != {'TimeGenerated','Log_s'}:
            raise ValueError('Exact minimal scoped log metadata required')
        observed = utc(row['TimeGenerated'])
        if not scheduled <= observed <= ended or type(row['Log_s']) is not str or len(row['Log_s']) > 8192:
            raise ValueError('Scoped log time/size differs')
        if row['Log_s'].startswith(CYCLE_PREFIX):
            cycles.append(json.loads(row['Log_s'][len(CYCLE_PREFIX):]))
        elif row['Log_s'].startswith(PREFIX):
            notices.append(json.loads(row['Log_s'][len(PREFIX):]))
        else:
            raise ValueError('Unregistered scoped log producer')
    if len(cycles) != 1 or len(notices) != 1:
        raise ValueError('Unique cycle/readiness pair required')
    cycle = cycles[0]
    expected = dict(catch_up_pending=0, coverage=COVERAGE, current_report_available=True,
        current_report_date=REPORT_DATE, database_access='verified', email_submitted=False,
        messages_sent=0, mode='scheduled', processed=[dict(already_published=False, event_count=6,
            fresh_login_readback_verified=True, report_date=REPORT_DATE, report_sha256=REPORT_SHA256)],
        source_mutation_authority=False, source_rows_changed=0, source_table_access=False,
        status='passed', website_release_verified=True)
    equal(cycle, expected, 'natural daily cycle')
    notice = notices[0]
    serialize_ready_event(notice)
    for key, wanted in dict(report_date=REPORT_DATE, report_sha256=REPORT_SHA256,
                           notification_key=NOTIFICATION_KEY).items():
        equal(notice.get(key), wanted, 'report readiness.'+key)
    if not scheduled <= utc(notice['recorded_at']) <= ended:
        raise ValueError('Readiness recorded outside successful execution')
    equal(ready_rows, [dict(NotificationKey=NOTIFICATION_KEY, ReportDate=REPORT_DATE,
        ReportHash=REPORT_SHA256, TableName='PrimaryResult')], 'registered ready query')
    equal(missing_rows, [], 'registered missed-deadline query')
    if type(alerts) is not list or len(alerts) > 25:
        raise ValueError('Bounded scoped alert inventory required')
    matching = []
    allowed_fields = {'alert_id','rule','severity','condition','started_at',
                      'last_modified','action_status','recipient_delivery_confirmed'}
    for alert in alerts:
        if type(alert) is not dict or set(alert) != allowed_fields:
            raise ValueError('Exact sanitized alert metadata required')
        rule = alert['rule']
        if type(rule) is not str:
            raise ValueError('Exact alert rule required')
        if rule.casefold() in (MISSING_RULE.casefold(),
                (BASE+'Microsoft.Insights/scheduledQueryRules/'+MISSING_RULE).casefold()):
            raise ValueError('A missed-deadline alert needs independent reconciliation')
        if rule.casefold() not in (RULE.casefold(), RULE_PATH.casefold()):
            raise ValueError('Unregistered sanitized alert')
        if (alert['recipient_delivery_confirmed'] is not False
                or type(alert['alert_id']) is not str
                or not re.fullmatch(re.escape(BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs'
                    '/providers/Microsoft.AlertsManagement/alerts/')+r'[0-9a-f-]{36}',
                    alert['alert_id'], flags=re.IGNORECASE)
                or alert['severity'] != 'Sev3' or alert['condition'] not in ('Fired','Resolved')
                or not utc(alert['started_at']) <= utc(alert['last_modified']) <= now):
            raise ValueError('Alert inventory cannot confirm recipient delivery')
        equal(alert['action_status'], {'isSuppressed':False}, 'ready alert action metadata')
        if str(alert['alert_id']).casefold() == NATURAL_ALERT_ID.casefold():
            if (alert['severity'] != 'Sev3' or alert['condition'] != 'Fired'
                    or not ended <= utc(alert['started_at']) <= scheduled+timedelta(minutes=15)
                    or not utc(alert['started_at']) <= utc(alert['last_modified']) <= now):
                raise ValueError('Exact unsuppressed natural report-ready alert required')
            matching.append(alert)
    if len(matching) != 1:
        raise ValueError('One exact natural report-ready alert required')
    return dict(cycle=cycle, ready_event=notice, ready_alert=matching[0],
                execution_started_at=scheduled.isoformat(), execution_ended_at=ended.isoformat())


def freeze_unchanged(before):
    if not before or source_manifest() != before:
        raise ValueError('Source freeze changed; no acceptance may be retained')


def collect():
    before = source_manifest()
    account = az('account','show')
    if (account.get('id') != SUB or account.get('tenantId') != ENTRA_TENANT
            or account.get('user') != {'name':SIGNED_ACCOUNT, 'type':'user'}):
        raise ValueError('Exact signed Azure account and subscription required')
    paths = ((FOLDER/'schedule-intent.json', 'daily-worker-schedule-intent',
              SCHEDULE_INTENT_SHA256, SCHEDULE_INTENT_VERSION),
             (FOLDER/'schedule-acceptance.json', 'daily-worker-schedule-completed',
              SCHEDULE_SHA256, SCHEDULE_VERSION),
             (ROOT/'.work/owner-task-postgres-20261003-retry1/source-checkpoint.json',
              'owner-task-pg-source-checkpoint', SOURCE_SHA256, SOURCE_VERSION))
    prerequisites, receipts = [], []
    for path, kind, sha, version in paths:
        record = json.loads(path.read_text(encoding='utf-8'))
        prerequisites.append(read_protected(record, kind, expected_sha=sha, expected_version=version))
        receipts.append(record['protected_receipt'])
    intent, acceptance, checkpoint = prerequisites
    validate_source_checkpoint(checkpoint)
    group_sha = intent['unaffected']['group_sha256']
    group = request('GET',GROUP_PATH+'?api-version=2023-01-01')
    validate_action_group(group, group_sha)
    for key, wanted in dict(status='passed', daily_schedule_enabled=True, cron_utc='0 10 * * *',
            time_zone='Asia/Hong_Kong', daily_hour=18, first_report_date=REPORT_DATE,
            payload_sha256=PAYLOAD_SHA256, immediate_retries=0, parallelism=1,
            natural_scheduled_execution_verified=False, six_autonomous_specialists_active=False,
            owner_codex_runtime_active=False).items():
        equal(acceptance.get(key), wanted, 'historical schedule acceptance.'+key)
    if bundle()[1] != PAYLOAD_SHA256:
        raise ValueError('Accepted deployed daily source changed')
    job = request('GET',job_path(JOB)+'?api-version=2024-03-01')
    validate(job, True)
    expected = intent['definition']['properties']['template']['containers'][0]
    if expected['image'] != IMAGE:
        raise ValueError('Protected scheduled image differs')
    run = request('GET',job_path(JOB)+'/executions/'+EXECUTION+'?api-version=2024-03-01')
    verify_container(run['properties']['template']['containers'], expected)
    logs = normalize_cli_log_rows(az('monitor','log-analytics','query','-w',WORKSPACE,'--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+EXECUTION+
        "' | where Log_s startswith '"+CYCLE_PREFIX+"' or Log_s startswith '"+PREFIX+
        "' | project TimeGenerated,Log_s"))
    ready_rule = request('GET',RULE_PATH+'?api-version=2023-12-01')
    validate_rule(ready_rule, ready_definition(True))
    missed_path = BASE+'Microsoft.Insights/scheduledQueryRules/'+MISSING_RULE
    missed_rule = request('GET',missed_path+'?api-version=2023-12-01')
    validate_rule(missed_rule, missing_definition(True))
    ready = az('monitor','log-analytics','query','-w',WORKSPACE,'--analytics-query',ready_query())
    missing = az('monitor','log-analytics','query','-w',WORKSPACE,'--analytics-query',missing_query())
    scope = BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs'
    query = urlencode(dict(**{'api-version':'2019-03-01'}, timeRange='1d', pageCount=25,
                           includeContext='false', includeEgressConfig='false'))
    inventory = request('GET',scope+'/providers/Microsoft.AlertsManagement/alerts?'+query)
    if inventory.get('nextLink') or type(inventory.get('value')) is not list or len(inventory['value']) > 25:
        raise ValueError('Bounded workspace alert inventory incomplete')
    alerts = []
    rules = {RULE.casefold(), RULE_PATH.casefold(), MISSING_RULE.casefold(), missed_path.casefold()}
    for item in inventory['value']:
        value = item['properties']['essentials']
        if str(value.get('alertRule','')).casefold() in rules:
            alerts.append(dict(alert_id=item['id'], rule=value['alertRule'], severity=value.get('severity'),
                condition=value.get('monitorCondition'), started_at=value.get('startDateTime'),
                last_modified=value.get('lastModifiedDateTime'),
                action_status={'isSuppressed':value.get('actionStatus',{}).get('isSuppressed')},
                recipient_delivery_confirmed=False))
    now = datetime.now(timezone.utc)
    observed = validate_cycle(run, logs, ready, missing, alerts, now=now)
    # Re-read configuration after evidence collection rather than only assuming
    # the first GET remained current. No values/commands enter retained metadata.
    final_job = request('GET',job_path(JOB)+'?api-version=2024-03-01')
    validate(final_job, True)
    settings = lambda value: digest({k:value[k] for k in ('identity','properties','tags')})
    if settings(final_job) != settings(job):
        raise ValueError('Scheduled configuration changed during observation')
    final_ready = request('GET',RULE_PATH+'?api-version=2023-12-01')
    final_missed = request('GET',missed_path+'?api-version=2023-12-01')
    final_group = request('GET',GROUP_PATH+'?api-version=2023-01-01')
    validate_action_group(final_group, group_sha)
    validate_rule(final_ready, ready_definition(True))
    validate_rule(final_missed, missing_definition(True))
    if (digest(final_ready['properties']) != digest(ready_rule['properties'])
            or digest(final_missed['properties']) != digest(missed_rule['properties'])):
        raise ValueError('Notification rules changed during observation')
    freeze_unchanged(before)
    return dict(status='passed', schema='first-natural-daily-cycle-acceptance-v1',
        checked_at_utc=now.isoformat(), execution=EXECUTION, report_date=REPORT_DATE,
        cron_utc='0 10 * * *', daily_hour_hkt=18, immediate_retries=0,
        frozen_payload_sha256=PAYLOAD_SHA256, image=IMAGE, settings_sha256=settings(job),
        source_checkpoint_receipt=receipts[2], schedule_intent_receipt=receipts[0],
        schedule_acceptance_receipt=receipts[1], sources=before, observed=observed,
        ready_rule_enabled=True, missing_rule_enabled=True, missing_query_rows=0,
        ready_rule_sha256=digest(ready_rule['properties']),
        missing_rule_sha256=digest(missed_rule['properties']),
        action_group_properties_sha256=group_sha, action_group_unchanged=True,
        configured_owner_receiver_enabled=True,
        natural_scheduled_execution_verified=True, recipient_delivery_confirmed=False,
        full_report_emailed=False, ibkr_source_writes=0, source_table_access=False,
        model_calls=0, runtime_activation_performed=False,
        source_checkpoint_owner_runtime_active=False,
        source_checkpoint_six_specialist_runtimes_active=False,
        independent_desktop_report_readback_verified=False,
        independent_desktop_ledger_readback_verified=False,
        independent_private_read_limit='No approved desktop private report/ledger reader; no credential or grant expansion.',
        coverage=COVERAGE, jobs_dispatched=0, database_connections=0, schedule_changes=0, rbac_changes=0)


def save(record):
    """One create-only evidence write, independently verified, no re-dispatch."""
    freeze_unchanged(record.get('sources'))
    # Leave even an empty reservation on uncertainty; no auto-retry/overwrite.
    with TARGET.open('x', encoding='utf-8') as output:
        archive_protection()
        receipt = retain(record, ACCEPTANCE_KIND)
        value = dict(record, protected_receipt=receipt)
        if read_protected(value, ACCEPTANCE_KIND) != record:
            raise ValueError('First natural-cycle protected acceptance differs')
        freeze_unchanged(record['sources'])
        output.write(json.dumps(value, sort_keys=True, indent=2, allow_nan=False))
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['inspect','retain'])
    args = parser.parse_args()
    if args.stage == 'retain' and TARGET.exists():
        raise ValueError('Acceptance reservation exists; inspect/reconcile, never overwrite')
    record = collect()
    if args.stage == 'retain':
        record = save(record)
    # The full source manifest lives only in protected/local acceptance. stdout
    # is bounded metadata, not commands, secrets, financial values or reasoning.
    print(json.dumps({k:v for k,v in record.items() if k != 'sources'}, sort_keys=True))


if __name__ == '__main__':
    main()
