"""Offline step-two launch and complete-wire oracles, not live SQL acceptance."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import owner_task_catalog_launch_v2 as launch
import owner_task_catalog_complete_logs as complete
import test_owner_task_provider_launch_contract as launches
import test_owner_task_catalog_family as families
import test_owner_task_catalog_logs as logs


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.inputs = launches.inputs()
        self.plan = launch.build(*self.inputs)
        self.job = launches.actual_job(self.plan['definition'])

    def validate(self, job):
        return launch.validate_job(job, *self.inputs,
            principal_id='11111111-1111-1111-1111-111111111111',
            client_id='22222222-2222-2222-2222-222222222222')

    def test_exact_separate_identity_none_version_pinned_secret_and_frozen_worker(self):
        old = launch.prior.build(*self.inputs)
        shadow = deepcopy(self.plan['definition'])
        identity = shadow['properties']['configuration'].pop('identitySettings')
        self.assertEqual(identity, [dict(identity=launch.registry.IDENTITY_ID, lifecycle='None')])
        self.assertEqual(shadow, old['definition'])
        self.assertEqual(self.plan['definition_sha256'], launch.digest(self.plan['definition']))
        self.assertEqual(launches.inline_source(self.plan), launches.inline_source(old))
        self.assertFalse(self.plan['dispatch_authorized'])

    def test_native_defaults_validated_on_copy_without_mutating_observations(self):
        job = self.job
        config = job['properties']['configuration']
        config['dapr'] = None
        config['registries'][0].update(username='', passwordSecretRef='')
        config['identitySettings'][0]['identity'] = launch.registry.IDENTITY_ID.replace('/resourceGroups/', '/resourcegroups/')
        job['properties']['eventStreamEndpoint'] = ('https://eastasia.azurecontainerapps.dev/subscriptions/'
            + launch.registry.SUBSCRIPTION + '/resourceGroups/' + launch.registry.RESOURCE_GROUP
            + '/containerApps/' + launch.registry.JOB + '/eventstream')
        job['properties']['outboundIpAddresses'] = ['20.1.2.3', '20.1.2.4']
        job['properties']['template']['containers'][0]['imageType'] = 'ContainerImage'
        job['systemData'] = dict(createdAt='2026-10-05T01:02:03.1234567', createdByType='User')
        before = deepcopy(job)
        result = self.validate(job)
        self.assertEqual(job, before)
        self.assertEqual(result['native_job_sha256'], launch.digest(before))
        self.assertFalse(result['identity_runtime_unavailability_verified'])
        self.assertFalse(result['restricted_login_verified'])

    def test_changes_to_identity_secret_runtime_and_native_defaults_rejected(self):
        mutations = (
            lambda j: j['properties']['configuration']['identitySettings'][0].update(lifecycle='All'),
            lambda j: j['properties']['configuration']['identitySettings'].append(dict(identity=launch.registry.IDENTITY_ID, lifecycle='None')),
            lambda j: j['properties']['configuration']['identitySettings'][0].update(identity='/foreign'),
            lambda j: j['properties']['configuration']['secrets'][0].update(keyVaultUrl='https://foreign'),
            lambda j: j['properties']['configuration']['registries'][0].update(username='admin'),
            lambda j: j['properties']['configuration'].update(dapr={}),
            lambda j: j['properties'].update(eventStreamEndpoint='https://foreign'),
            lambda j: j['properties']['template']['containers'][0].update(imageType='Other'),
            lambda j: j['properties']['template']['containers'][0]['env'].append(dict(name='PGPASSWORD', value='synthetic')),
            lambda j: j.update(systemData=dict(createdAt='2026-02-30T01:02:03.1234567')),
            lambda j: j.update(systemData=dict(createdAt='2026-10-05T01:02:03.1234567Z')),
            lambda j: j.update(systemData=dict(unreviewed='field')),
        )
        for mutate in mutations:
            value = deepcopy(self.job); mutate(value)
            with self.subTest(value=mutate), self.assertRaises(ValueError):
                self.validate(value)

    def test_execution_template_only_accepts_native_container_image_and_known_secret_alias(self):
        value = deepcopy(self.job['properties']['template'])
        value['containers'][0]['imageType'] = 'ContainerImage'
        value['containers'][0]['env'][0]['secretRef'] = 'cappjob-' + launch.registry.JOB
        launch.validate_template(value, *self.inputs, execution=True)
        with self.assertRaises(ValueError):
            launch.validate_template(value, *self.inputs, execution=False)


class CompleteEvidenceTests(unittest.TestCase):
    def collect(self, fixture):
        return complete.collect_evidence(fixture.family_query, fixture.captured_query, fixture.scope)

    def test_unfiltered_header_query_has_no_container_stream_or_protocol_filter(self):
        query = complete.CompleteQuery(families.scope())
        self.assertIn("ContainerGroupName_s startswith 'ibkrv2stg-ownertrial-catalog-9hh541u-'", query.text)
        for forbidden in ("| where Log_s", "ContainerName_s ==", "Stream_s ==", '| take ', '| distinct ', '| summarize '):
            self.assertNotIn(forbidden, query.text)
        with self.assertRaises(FrozenInstanceError):
            query.scope = families.scope()

    def test_complete_bracketing_and_exact_captured_wire(self):
        f = families.FamilyFixture()
        result = self.collect(f)
        self.assertEqual(f.events, ['family', 'inventory', 'payload', 'inventory', 'family'])
        self.assertTrue(result['observed_all_container_console_complete'])
        for field in ('late_delivery_excluded', 'platform_replacement_excluded', 'zero_log_replica_absence_verified'):
            self.assertFalse(result[field])
        self.assertEqual(result['captured']['record'], f.captured.record)
        self.assertEqual(result['inventory_sha256'], result['captured']['header_inventory_sha256'])

    def test_extra_duplicate_foreign_container_and_ordinary_log_are_not_hidden(self):
        for kind in ('duplicate', 'foreign-container', 'foreign-replica', 'ordinary'):
            f = families.FamilyFixture()
            extra = deepcopy(f.before[0])
            if kind == 'foreign-container': extra[1] = 'foreign-container'
            if kind == 'foreign-replica': extra[0] = f.scope.execution_name + '-other'
            if kind == 'ordinary': extra[3] = 'ordinary nonprotocol stderr'
            f.before.append(extra)
            with self.subTest(kind=kind), self.assertRaises(complete.reader.CatalogLogsError):
                self.collect(f)
            self.assertEqual(f.captured.requests, [])

    def test_disappearing_or_changed_after_inventory_prevents_acceptance(self):
        for action in ('drop', 'hash', 'duplicate'):
            f = families.FamilyFixture()
            if action == 'drop': f.after.pop()
            elif action == 'hash': f.after[0][5] = 'f' * 64
            else: f.after.append(deepcopy(f.after[0]))
            with self.subTest(action=action), self.assertRaises(complete.reader.CatalogLogsError):
                self.collect(f)

    def test_partial_error_or_unexpected_table_rejected(self):
        for mutate in (lambda v: v.update(error=dict(code='PartialError')),
            lambda v: v['tables'].append(deepcopy(v['tables'][0])),
            lambda v: v['tables'][0]['columns'].pop()):
            f = families.FamilyFixture(); f.response_mutation = lambda v: (mutate(v), v)[1]
            with self.assertRaises(complete.reader.CatalogLogsError): self.collect(f)
            self.assertEqual(f.captured.requests, [])

    def test_dynamic_parts_reconstruct_actual_four_mib_record_below_storage_caps(self):
        target = families.scope()
        codec = complete.reader.codec
        record = {'data': 'x' * (codec.MAX_RECORD_BYTES - len(codec.canonical({'data': ''})))}
        self.assertEqual(len(codec.canonical(record)), codec.MAX_RECORD_BYTES)
        lines = codec.encode_record(record, target.replica_scope.binding)
        self.assertEqual(len(lines), 1025)
        receipt = dict(blob='synthetic-only', sha256='a' * 64)
        parts = complete.wire_parts(lines, target.replica_scope.binding, target.execution_name, receipt)
        self.assertEqual(len(parts), 9)
        self.assertEqual(len(parts[-1]['lines']), 1)
        self.assertTrue(all(len(codec.canonical(part)) <= complete.reader.MAX_RESPONSE_BYTES for part in parts))
        self.assertEqual(complete.reconstruct(parts, target.replica_scope.binding, target.execution_name, receipt), record)

    def test_part_chain_changes_and_incomplete_parts_rejected(self):
        target = families.scope(); binding = target.replica_scope.binding
        lines = complete.reader.codec.encode_record({'data': 'x' * 600000}, binding)
        receipt = dict(blob='synthetic-only', sha256='a' * 64)
        parts = complete.wire_parts(lines, binding, target.execution_name, receipt)
        changes = (lambda p: p.reverse(), lambda p: p.pop(),
            lambda p: p[0].update(index=False), lambda p: p[0].update(offset=True),
            lambda p: p[0].update(execution_id='foreign'), lambda p: p[0].update(replica_receipt={}),
            lambda p: p[0]['binding'].update(source_sha256='f' * 64),
            lambda p: p[0]['lines'].pop(), lambda p: p[0].update(extra=True))
        for mutate in changes:
            value = deepcopy(parts); mutate(value)
            with self.subTest(mutation=mutate), self.assertRaises((ValueError, complete.reader.CatalogLogsError)):
                complete.reconstruct(value, binding, target.execution_name, receipt)


if __name__ == '__main__': unittest.main()
