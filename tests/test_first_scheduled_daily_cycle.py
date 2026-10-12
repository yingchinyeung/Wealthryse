"""Synthetic pure linkage/readback fixtures; no live cycle or SQL acceptance."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import verify_first_scheduled_daily_cycle as first


def evidence():
    cycle = dict(catch_up_pending=0, coverage='specialist-ledger-only; complete-system capture not established',
        current_report_available=True, current_report_date='2026-10-03', database_access='verified',
        email_submitted=False, messages_sent=0, mode='scheduled',
        processed=[dict(already_published=False, event_count=6, fresh_login_readback_verified=True,
            report_date='2026-10-03', report_sha256='cec90e711932419324d088e6e11a3cf8bc6a4dd93d72079ac380ec0bc05897ed')],
        source_mutation_authority=False, source_rows_changed=0, source_table_access=False,
        status='passed', website_release_verified=True)
    ready = dict(schema='specialist-report-ready-v1', tenant_id='00000000-0000-0000-0000-000000000001',
        report_date='2026-10-03', report_sha256=cycle['processed'][0]['report_sha256'],
        notification_key='2db447cf964d2cc0f007c864819027e2d065da36984622737c199264dee5dd21',
        recorded_at='2026-10-03T10:00:52.345638+00:00', website_state='available',
        recipient_delivery_confirmed=False)
    logs = [dict(TimeGenerated='2026-10-03T10:00:54.2962626Z',
                 Log_s='SPECIALIST_DAILY_CYCLE '+json.dumps(cycle)),
            dict(TimeGenerated='2026-10-03T10:00:54.2962626Z',
                 Log_s='SPECIALIST_REPORT_READY '+json.dumps(ready))]
    run = dict(name='ibkrv2stg-agent-report-29850360', properties=dict(status='Succeeded',
        startTime='2026-10-03T10:00:00+00:00', endTime='2026-10-03T10:00:56+00:00'))
    ready_rows = [dict(NotificationKey=ready['notification_key'], ReportDate='2026-10-03',
                       ReportHash=ready['report_sha256'], TableName='PrimaryResult')]
    alert = dict(alert_id=first.NATURAL_ALERT_ID, rule=first.RULE_PATH,
        severity='Sev3', condition='Fired', started_at='2026-10-03T10:02:05.5396924Z',
        last_modified='2026-10-03T10:02:05.5396924Z', action_status={'isSuppressed':False},
        recipient_delivery_confirmed=False)
    return [run, logs, ready_rows, [], [alert]]


def replace_log(values, index, key, value):
    prefix = first.CYCLE_PREFIX if index == 0 else first.PREFIX
    record = json.loads(values[1][index]['Log_s'][len(prefix):])
    record[key] = value
    values[1][index]['Log_s'] = prefix+json.dumps(record)


def retained(value, kind=first.ACCEPTANCE_KIND):
    sha = first.digest(value)
    return dict(value, protected_receipt=dict(blob=f'acceptance/gateway-read-{kind}-{sha}.json',
        sha256=sha, version_id='2026-10-03T23:00:00.1234567Z', protected_readback_verified=True))


class FirstScheduledDailyCycleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,10,3,23,0,0,tzinfo=timezone.utc)

    def validate(self, values):
        return first.validate_cycle(*values, now=self.now)

    def reject(self, values):
        with self.assertRaises((ValueError, TypeError)):
            self.validate(values)

    def test_exact_metadata_links_one_cycle_ready_hash_and_alert_without_io(self):
        values = evidence()
        before = copy.deepcopy(values)
        with (mock.patch.object(first, 'request', side_effect=AssertionError('no HTTP')),
              mock.patch.object(first, 'az', side_effect=AssertionError('no CLI')),
              mock.patch.object(first, 'retain', side_effect=AssertionError('no archive write'))):
            result = self.validate(values)
        self.assertEqual(result['cycle']['processed'][0]['event_count'], 6)
        self.assertFalse(result['ready_alert']['recipient_delivery_confirmed'])
        self.assertFalse(result['ready_event']['recipient_delivery_confirmed'])
        self.assertEqual(values, before)

    def test_only_measured_cli_wrapper_is_removed_before_unchanged_pure_parser(self):
        values = evidence()
        wrapped = [dict(row,TableName='PrimaryResult') for row in values[1]]
        unchanged = copy.deepcopy(wrapped)
        with (mock.patch.object(first,'request',side_effect=AssertionError('no HTTP')),
              mock.patch.object(first,'az',side_effect=AssertionError('no CLI'))):
            normalized = first.normalize_cli_log_rows(wrapped)
            self.assertEqual(normalized,values[1])
            self.assertEqual(wrapped,unchanged)
            values[1] = normalized
            self.validate(values)
            # The parser itself still rejects even the known transport field.
            values[1] = wrapped
            self.reject(values)
        cases = ('missing_table','wrong_table','numeric_table','boolean_table',
                 'extra_field','nonstr_log','nonstr_time','missing_row','extra_row','not_list')
        for case in cases:
            with self.subTest(case=case):
                rows = copy.deepcopy(wrapped)
                if case == 'missing_table': del rows[0]['TableName']
                elif case == 'wrong_table': rows[0]['TableName'] = 'OtherResult'
                elif case == 'numeric_table': rows[0]['TableName'] = 0
                elif case == 'boolean_table': rows[0]['TableName'] = False
                elif case == 'extra_field': rows[0]['extra'] = 'must not be retained'
                elif case == 'nonstr_log': rows[0]['Log_s'] = None
                elif case == 'nonstr_time': rows[0]['TimeGenerated'] = 1
                elif case == 'missing_row': rows.pop()
                elif case == 'extra_row': rows.append(copy.deepcopy(rows[0]))
                else: rows = tuple(rows)
                with self.assertRaises(ValueError):
                    first.normalize_cli_log_rows(rows)

    def test_execution_name_status_start_and_end_are_exact(self):
        cases = [('name','ibkrv2stg-agent-report-manual'), ('status','Failed'),
                 ('startTime','2026-10-03T10:01:00Z'), ('startTime','2026-10-03T18:00:00+08:00'),
                 ('endTime','2026-10-03T09:59:59Z'), ('endTime','2026-10-03T10:15:01Z')]
        for key,value in cases:
            with self.subTest(key=key, value=value):
                values = evidence()
                (values[0] if key == 'name' else values[0]['properties'])[key] = value
                self.reject(values)

    def test_collect_window_must_be_current_first_closed_report(self):
        for now in (datetime(2026,10,3,9,59,59,tzinfo=timezone.utc),
                    datetime(2026,10,4,10,0,0,tzinfo=timezone.utc), datetime(2026,10,3,23,0,0)):
            with self.subTest(now=now):
                with self.assertRaises(ValueError):
                    first.validate_cycle(*evidence(), now=now)

    def test_unique_bounded_scoped_log_pair_required(self):
        cases = ('missing','duplicate','swapped_producer','extra_field','too_large','before_start','after_end')
        for case in cases:
            with self.subTest(case=case):
                values = evidence()
                if case == 'missing': values[1].pop()
                elif case == 'duplicate': values[1].append(copy.deepcopy(values[1][0]))
                elif case == 'swapped_producer': values[1][1] = copy.deepcopy(values[1][0])
                elif case == 'extra_field': values[1][0]['financial_payload'] = 'must not enter evidence'
                elif case == 'too_large': values[1][0]['Log_s'] = first.CYCLE_PREFIX+'x'*8193
                elif case == 'before_start': values[1][0]['TimeGenerated'] = '2026-10-03T09:59:59Z'
                else: values[1][0]['TimeGenerated'] = '2026-10-03T10:00:57Z'
                self.reject(values)

    def test_cycle_flags_counts_and_coverage_are_strict_json_types(self):
        cases = [('source_rows_changed',False), ('messages_sent',False), ('catch_up_pending',False),
            ('source_table_access',0), ('source_mutation_authority',0), ('email_submitted',0),
            ('current_report_available',1), ('website_release_verified',1),
            ('coverage','complete-system'), ('mode','rehearsal'), ('unknown','extra')]
        for key,value in cases:
            with self.subTest(key=key, value=value):
                values = evidence()
                replace_log(values,0,key,value)
                self.reject(values)

    def test_processed_report_identity_hash_count_and_fresh_read_are_bound(self):
        for key,value in (('report_date','2026-10-02'), ('report_sha256','f'*64),
                          ('event_count',True), ('event_count',7),
                          ('already_published',True), ('fresh_login_readback_verified',1)):
            with self.subTest(key=key):
                values = evidence()
                cycle = json.loads(values[1][0]['Log_s'][len(first.CYCLE_PREFIX):])
                cycle['processed'][0][key] = value
                values[1][0]['Log_s'] = first.CYCLE_PREFIX+json.dumps(cycle)
                self.reject(values)

    def test_ready_event_scope_hash_date_key_and_email_boundary_required(self):
        for key,value in (('tenant_id','00000000-0000-0000-0000-000000000002'),
            ('report_date','2026-10-02'), ('report_sha256','f'*64), ('notification_key','f'*64),
            ('recipient_delivery_confirmed',0), ('recipient_delivery_confirmed',True),
            ('recorded_at','2026-10-03T10:00:57Z'), ('unknown','extra')):
            with self.subTest(key=key,value=value):
                values = evidence()
                replace_log(values,1,key,value)
                self.reject(values)

    def test_ready_and_missing_query_results_cannot_be_omitted_or_aliased(self):
        for index,value in ((2,[]), (2,evidence()[2]*2), (3,[{'ReportDate':'2026-10-03'}]), (3,False)):
            with self.subTest(index=index,value=value):
                values = evidence()
                values[index] = value
                self.reject(values)
        for key,value in (('ReportHash','f'*64), ('NotificationKey','f'*64),
                          ('ReportDate','2026-10-02'), ('TableName','AnotherResult')):
            values = evidence()
            values[2][0][key] = value
            self.reject(values)

    def test_exact_unsuppressed_natural_alert_is_not_recipient_confirmation(self):
        for key,value in (('alert_id',first.NATURAL_ALERT_ID[:-1]+'2'), ('rule','arbitrary'),
            ('condition','Resolved'), ('severity','Sev2'), ('action_status',{'isSuppressed':True}),
            ('action_status',{'isSuppressed':0}), ('action_status',{'isSuppressed':False,'extra':'text'}),
            ('recipient_delivery_confirmed',True), ('started_at','2026-10-03T10:16:00Z')):
            with self.subTest(key=key,value=value):
                values = evidence()
                values[4][0][key] = value
                self.reject(values)
        for alerts in ([], evidence()[4]*2):
            values = evidence()
            values[4] = alerts
            self.reject(values)

    def test_old_resolved_ready_alert_allowed_but_missed_alert_not_silenced(self):
        values = evidence()
        old = copy.deepcopy(values[4][0])
        old.update(alert_id=first.NATURAL_ALERT_ID[:-36]+'b16a06bf-ed21-3414-72a6-9809ea5a001c',
            condition='Resolved',started_at='2026-10-03T01:22:06Z',last_modified='2026-10-03T10:13:03Z')
        values[4].append(old)
        self.validate(values)
        values[4][1]['rule'] = first.MISSING_RULE
        self.reject(values)

    def test_receipt_is_exact_hash_kind_version_and_protection_bound(self):
        value = {'status':'passed','synthetic':True}
        record = retained(value)
        actual,payload = first.validate_receipt(record, first.ACCEPTANCE_KIND)
        self.assertEqual(actual,value)
        self.assertEqual(hashlib.sha256(payload).hexdigest(),record['protected_receipt']['sha256'])
        for key,item in (('sha256','f'*64), ('blob','another-container/artifact'),
                         ('version_id','2026-10-03T23:00:00Z'), ('protected_readback_verified',1)):
            with self.subTest(key=key):
                changed = copy.deepcopy(record)
                changed['protected_receipt'][key] = item
                with self.assertRaises(ValueError):
                    first.validate_receipt(changed,first.ACCEPTANCE_KIND)
        with self.assertRaises(ValueError):
            first.validate_receipt(record,'unregistered-kind')
        with self.assertRaises(ValueError):
            first.validate_receipt(record,first.ACCEPTANCE_KIND,expected_sha='f'*64)
        with self.assertRaises(ValueError):
            first.validate_receipt(record,first.ACCEPTANCE_KIND,expected_version='2026-10-03T23:00:01.1234567Z')

    def test_exact_version_and_etag_conditional_remote_readback(self):
        record = retained({'synthetic':True})
        payload = first.canonical({'synthetic':True})
        blob = mock.Mock()
        blob.get_blob_properties.return_value = SimpleNamespace(size=len(payload),
            version_id=record['protected_receipt']['version_id'],etag='synthetic-etag')
        blob.download_blob.return_value.readall.return_value = payload
        service = mock.Mock()
        service.get_blob_client.return_value = blob
        with (mock.patch.object(first,'BlobServiceClient',return_value=service) as factory,
              mock.patch.object(first,'AzureCliCredential',return_value='synthetic-credential'),
              mock.patch.object(first,'archive_protection') as protection):
            self.assertEqual(first.read_protected(record,first.ACCEPTANCE_KIND),{'synthetic':True})
        factory.assert_called_once_with('https://'+first.ACCOUNT+'.blob.core.windows.net',
                                        credential='synthetic-credential')
        service.get_blob_client.assert_called_once_with(first.CONTAINER,
            record['protected_receipt']['blob'],version_id=record['protected_receipt']['version_id'])
        blob.download_blob.assert_called_once_with(offset=0,length=len(payload)+1,etag='synthetic-etag',
            match_condition=first.MatchConditions.IfNotModified)
        self.assertEqual(protection.call_count,2)

    def test_mismatched_remote_bytes_size_version_or_etag_cannot_pass(self):
        record = retained({'synthetic':True})
        payload = first.canonical({'synthetic':True})
        for field,item in (('bytes',b'altered'), ('size',True), ('size',1),
                           ('version_id','different-version'), ('etag',None)):
            with self.subTest(field=field):
                props = dict(size=len(payload),version_id=record['protected_receipt']['version_id'],etag='etag')
                returned = item if field == 'bytes' else payload
                if field != 'bytes': props[field] = item
                blob = mock.Mock()
                blob.get_blob_properties.return_value = SimpleNamespace(**props)
                blob.download_blob.return_value.readall.return_value = returned
                service = mock.Mock()
                service.get_blob_client.return_value = blob
                with (mock.patch.object(first,'BlobServiceClient',return_value=service),
                      mock.patch.object(first,'AzureCliCredential'),
                      mock.patch.object(first,'archive_protection')):
                    with self.assertRaises(ValueError):
                        first.read_protected(record,first.ACCEPTANCE_KIND)

    def test_historical_source_record_keeps_runtime_and_old_cycle_flags_false(self):
        value = dict(status='validated-source-live-sql-pending',ibkr_source_writes=False,model_calls=0,
            owner_runtime_active=False,six_specialist_runtimes_active=False,
            production_migration_059_installed=False,natural_daily_cycle_verified=False)
        first.validate_source_checkpoint(value)
        for key,item in (('ibkr_source_writes',0), ('model_calls',False),
                         ('owner_runtime_active',0), ('six_specialist_runtimes_active',True),
                         ('natural_daily_cycle_verified',True)):
            changed = dict(value,**{key:item})
            with self.assertRaises(ValueError):
                first.validate_source_checkpoint(changed)

    def test_embedded_historical_shapes_need_no_private_work_artifacts(self):
        # Independent minimal historical-shape oracle. Real retained records
        # are checked by live collect, never bundled into portable unittest CI.
        source = dict(status='validated-source-live-sql-pending',ibkr_source_writes=False,model_calls=0,
            owner_runtime_active=False,six_specialist_runtimes_active=False,
            production_migration_059_installed=False,natural_daily_cycle_verified=False)
        intent = dict(definition={'properties':{'template':{'containers':[
            {'image':first.IMAGE}]} }},unaffected={'group_sha256':'a'*64})
        schedule = dict(status='passed',payload_sha256=first.PAYLOAD_SHA256,
            cron_utc='0 10 * * *',natural_scheduled_execution_verified=False)
        fixtures = [('owner-task-pg-source-checkpoint',source,first.SOURCE_VERSION),
            ('daily-worker-schedule-intent',intent,first.SCHEDULE_INTENT_VERSION),
            ('daily-worker-schedule-completed',schedule,first.SCHEDULE_VERSION)]
        with mock.patch.object(Path,'read_text',side_effect=AssertionError('no local artifacts')):
            for kind,value,version in fixtures:
                raw = json.dumps(value,sort_keys=True,separators=(',',':')).encode()
                sha = hashlib.sha256(raw).hexdigest()
                record = dict(value,protected_receipt=dict(sha256=sha,version_id=version,
                    protected_readback_verified=True,blob=f'acceptance/gateway-read-{kind}-{sha}.json'))
                actual,payload = first.validate_receipt(record,kind,expected_sha=sha,expected_version=version)
                self.assertEqual(actual,value)
                self.assertEqual(payload,raw)
                if kind == 'owner-task-pg-source-checkpoint':
                    first.validate_source_checkpoint(actual)
                    self.assertIs(actual['ibkr_source_writes'],False)

    def test_action_group_enabled_receiver_and_exact_protected_properties_hash(self):
        group = dict(id=first.GROUP_PATH,properties=dict(enabled=True,
            emailReceivers=[dict(emailAddress='anthony.ying@wealthryse.ai',status='Enabled')]))
        sha = first.digest(group['properties'])
        self.assertEqual(first.validate_action_group(group,sha),sha)
        for change in ('id','enabled','receiver','status','extra_properties','hash'):
            with self.subTest(change=change):
                value = copy.deepcopy(group)
                expected = sha
                if change == 'id': value['id'] += '-wrong'
                elif change == 'enabled': value['properties']['enabled'] = 1
                elif change == 'receiver': value['properties']['emailReceivers'][0]['emailAddress'] = 'other@example.com'
                elif change == 'status': value['properties']['emailReceivers'][0]['status'] = 'Disabled'
                elif change == 'extra_properties': value['properties']['newReceiver'] = True
                else: expected = 'f'*64
                with self.assertRaises(ValueError):
                    first.validate_action_group(value,expected)

    def test_source_change_fails_before_any_evidence_write(self):
        with (mock.patch.object(first,'source_manifest',return_value={'x':'changed'}),
              mock.patch.object(first,'retain') as writer):
            with self.assertRaises(ValueError):
                first.save({'sources':{'x':'original'}})
            writer.assert_not_called()

    def test_create_only_save_independently_reads_exact_retained_bytes(self):
        record = dict(status='passed', sources={'synthetic.py':'a'*64})
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'acceptance.json'
            receipt = retained(record)['protected_receipt']
            with (mock.patch.object(first,'TARGET',target),
                  mock.patch.object(first,'source_manifest',return_value=record['sources']),
                  mock.patch.object(first,'archive_protection'),
                  mock.patch.object(first,'retain',return_value=receipt) as writer,
                  mock.patch.object(first,'read_protected',return_value=record) as reader):
                result = first.save(record)
                self.assertEqual(json.loads(target.read_text()),result)
                writer.assert_called_once_with(record,first.ACCEPTANCE_KIND)
                reader.assert_called_once_with(result,first.ACCEPTANCE_KIND)
                with self.assertRaises(FileExistsError):
                    first.save(record)
                self.assertEqual(writer.call_count,1)

    def test_uncertain_write_readback_or_postfreeze_leaves_reservation_no_retry(self):
        record = dict(status='passed',sources={'synthetic.py':'a'*64})
        for stage in ('write','readback','changed_after_readback'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as folder:
                target = Path(folder)/'acceptance.json'
                receipt = retained(record)['protected_receipt']
                manifests = [record['sources'],{'synthetic.py':'b'*64}] if stage == 'changed_after_readback' else None
                with (mock.patch.object(first,'TARGET',target),
                      mock.patch.object(first,'source_manifest',side_effect=manifests,
                                        return_value=record['sources']),
                      mock.patch.object(first,'archive_protection'),
                      mock.patch.object(first,'retain',return_value=receipt,
                                        side_effect=RuntimeError('uncertain') if stage == 'write' else None) as writer,
                      mock.patch.object(first,'read_protected',return_value=record,
                                        side_effect=ValueError('differs') if stage == 'readback' else None)):
                    with self.assertRaises((RuntimeError,ValueError)):
                        first.save(record)
                    self.assertTrue(target.exists())
                    self.assertEqual(target.read_bytes(),b'')
                    with self.assertRaises(FileExistsError):
                        # Stabilize only the fixture manifest; never the source
                        # acceptance. Existing reservation forbids a new upload.
                        with mock.patch.object(first,'source_manifest',return_value=record['sources']):
                            first.save(record)
                    self.assertEqual(writer.call_count,1)


if __name__ == '__main__':
    unittest.main()
