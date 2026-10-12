"""Isolated runtime actions cannot become persistent cutovers or retries."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import qualify_ms_presentation_worker_runtime as controller


class RuntimeProbeControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'alpha-ms-presentation-worker-candidate-20261007-r5'
        self.folder.mkdir()
        self.runtime = self.folder / 'runtime-v1'
        self.runtime.mkdir()
        for name in ('source-intent', 'scan-completed'):
            (self.folder / (name + '.json')).write_text('{}')
        (self.runtime / 'frozen-probe.py').write_text('pass\n')
        intent = {'probe_sha256': controller.build.sha(self.runtime / 'frozen-probe.py'),
            'source_intent_sha256': controller.build.sha(self.folder / 'source-intent.json'),
            'scan_completed_sha256': controller.build.sha(self.folder / 'scan-completed.json'),
            'image': 'registry/candidate@sha256:' + '1' * 64,
            'base_image': 'registry/base@sha256:' + '2' * 64,
            'periods': {}, 'installed_files_sha256': {},
            'output_prefix': controller.runtime_prefix(self.folder),
            'content_contract_version': controller.WORKBOOK_CONTRACT}
        (self.runtime / 'source-intent.json').write_text(json.dumps(intent))
        self.job = {'properties': {'template': {'containers': [{
            'name': 'report', 'image': intent['base_image'], 'command': ['python'], 'args': ['production.py'],
            'env': [{'name': 'DATABASE_URL', 'secretRef': 'report-db'},
                    {'name': 'AZURE_STORAGE_ACCOUNT_URL', 'value': 'https://storage.blob.core.windows.net'}],
            'resources': {'cpu': 1, 'memory': '2Gi'}}]}}}

    def dispatch(self, *, busy=False):
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == 'GET' and '/executions?' in path:
                return {'value': [{'properties': {'status': 'Running'}}]} if busy else {'value': []}
            if method == 'GET':
                return self.job
            if method == 'POST' and '/start?' in path:
                return {'name': 'isolated-run'}
            raise AssertionError('Persistent mutation attempted')

        with patch.object(controller, 'request', side_effect=request), \
            patch.object(controller, 'retain', return_value={'protected_readback_verified': True}), \
            contextlib.redirect_stdout(io.StringIO()):
            controller.dispatch(self.folder, self.runtime, 'candidate')
        return calls

    def test_only_execution_image_and_command_change(self):
        original = json.dumps(self.job, sort_keys=True)
        calls = self.dispatch()
        posts = [call for call in calls if call[0] == 'POST']
        self.assertEqual(len(posts), 1)
        self.assertIn('/start?', posts[0][1])
        actual = posts[0][2]['data']['containers'][0]
        expected = self.job['properties']['template']['containers'][0]
        self.assertEqual(actual['env'], expected['env'])
        self.assertEqual(actual['resources'], expected['resources'])
        self.assertNotEqual(actual['image'], expected['image'])
        self.assertEqual(json.dumps(self.job, sort_keys=True), original)

    def test_dispatch_cannot_be_repeated(self):
        self.dispatch()
        with patch.object(controller, 'request') as request, patch.object(controller, 'retain') as retain:
            with self.assertRaises(ValueError):
                controller.dispatch(self.folder, self.runtime, 'candidate')
            request.assert_not_called()
            retain.assert_not_called()

    def test_busy_worker_refuses_dispatch(self):
        with self.assertRaises(ValueError):
            self.dispatch(busy=True)
        self.assertFalse((self.runtime / 'candidate-dispatch-intent.json').exists())

    def test_changed_probe_is_rejected(self):
        (self.runtime / 'frozen-probe.py').write_text('changed')
        with patch.object(controller, 'request') as request:
            with self.assertRaises(ValueError):
                controller.dispatch(self.folder, self.runtime, 'candidate')
            request.assert_not_called()

    def test_plaintext_database_setting_is_rejected(self):
        self.job['properties']['template']['containers'][0]['env'][0] = {
            'name': 'DATABASE_URL', 'value': 'postgresql://not-a-secret-reference'}
        with self.assertRaises(ValueError):
            self.dispatch()

    def test_new_prefix_is_candidate_specific_and_cannot_reuse_r2(self):
        self.assertEqual(controller.runtime_prefix(self.folder),
                         'review/alpha-world-ms/presentation-worker-r5-20261007/')
        intent = controller.read(self.runtime, 'source-intent')
        intent['output_prefix'] = controller.PREFIX
        (self.runtime / 'source-intent.json').write_text(json.dumps(intent))
        with patch.object(controller, 'request') as request, self.assertRaisesRegex(ValueError, 'prefix or workbook contract'):
            self.dispatch()
        request.assert_not_called()

    def test_legacy_contract_cannot_dispatch_new_candidate(self):
        intent = controller.read(self.runtime, 'source-intent')
        intent['content_contract_version'] = 'alpha-world-ms-v24-hkd-hibor-evidence-v1'
        (self.runtime / 'source-intent.json').write_text(json.dumps(intent))
        with patch.object(controller, 'request') as request, self.assertRaisesRegex(ValueError, 'prefix or workbook contract'):
            self.dispatch()
        request.assert_not_called()

    def test_format_candidate_uses_separate_create_only_namespace(self):
        format_folder = Path('alpha-ms-format-worker-candidate-20261008-r4')
        self.assertEqual(controller.runtime_prefix(format_folder),
                         'review/alpha-world-ms/format-worker-r4-20261008/')
        self.assertNotEqual(controller.runtime_prefix(format_folder),
                            controller.runtime_prefix(Path('alpha-ms-presentation-worker-candidate-20261008-r4')))

    def test_format_baseline_is_exact_protected_installed_check(self):
        path = Path(self.temp.name) / '.work/alpha-ms-presentation-worker-candidate-20261007-r5/deployment-v1'
        path.mkdir(parents=True)
        image = 'registry/base@sha256:' + '2' * 64
        record = {'status': 'passed', 'job': controller.JOB, 'image': image,
                  'execution': 'installed-check'}
        digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        record['protected_receipt'] = {'sha256': digest, 'version_id': 'version',
                                       'protected_readback_verified': True}
        receipt = path / (controller.JOB + '-check-qualified.json')
        receipt.write_text(json.dumps(record))
        folder = Path('alpha-ms-format-worker-candidate-20261008-r4')
        with patch.object(controller.build, 'ROOT', Path(self.temp.name)):
            self.assertEqual(controller.installed_baseline(folder, image)['execution'], 'installed-check')
            with self.assertRaises(ValueError):
                controller.installed_baseline(folder, 'different-image')
            record['execution'] = 'tampered-check'
            receipt.write_text(json.dumps(record))
            with self.assertRaises(ValueError):
                controller.installed_baseline(folder, image)

    def test_runtime_namespace_rejects_unknown_kind_invalid_date_or_revision(self):
        for name in ('alpha-ms-other-worker-candidate-20261008-r4',
                     'alpha-ms-format-worker-candidate-20261308-r4',
                     'alpha-ms-format-worker-candidate-20261008-r0',
                     'alpha-ms-format-worker-candidate-20261008-r4-extra'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                controller.runtime_prefix(Path(name))

    def test_incomplete_or_wrong_output_workbook_proof_is_rejected(self):
        row = {'presentation_sha256': '1'*64, 'embedded_workbooks_verified': 6,
               'presentation_validation': {'content_contract_version': controller.WORKBOOK_CONTRACT,
                'output_sha256': '1'*64, 'ms_chart_workbook_audit': {'status': 'passed',
                'embedded_workbooks_verified': 6, 'owner_slides': [6,7,8,9,10,11],
                'output_sha256': '1'*64, 'items': [{}]*6}}}
        controller.require_workbook_proof(row)
        for key, value in [('output_sha256', 'wrong'), ('embedded_workbooks_verified', 5),
                           ('status', 'failed'), ('items', []), ('owner_slides', [6,7,8,9,10])]:
            changed = copy.deepcopy(row)
            changed['presentation_validation']['ms_chart_workbook_audit'][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'output-bound'):
                controller.require_workbook_proof(changed)

    def test_probe_enforces_read_only_before_rendering(self):
        source = (controller.build.ROOT / 'scripts/verify_ms_presentation_worker_runtime.py').read_text()
        self.assertIn('default_transaction_read_only=on', source)
        self.assertIn('REPEATABLE READ READ ONLY', source)
        self.assertLess(source.index("SHOW transaction_read_only"), source.index('render_factsheet(payload'))
        self.assertIn('raw.rollback()', source)
        self.assertNotIn('raw.commit()', source)

    def test_verified_service_projection_only(self):
        expected = copy.deepcopy(self.job['properties']['template']['containers'][0])
        expected['env'][0].update(secretRef='database-url', value=None)
        actual = copy.deepcopy(expected)
        actual['env'][0].pop('value')
        actual['env'][0]['secretRef'] = 'cappjob-' + controller.JOB
        actual['resources']['ephemeralStorage'] = ''
        identity = 'existing-managed-identity'
        job = {'identity': {'userAssignedIdentities': {identity: {}}}, 'properties': {'configuration':
            {'secrets': [{'name': 'database-url', 'keyVaultUrl': 'https://ibkrv2stg-auhecwc4itaoo.vault.azure.net/secrets/database-app-url', 'identity': identity}]}}}
        baseline = copy.deepcopy(actual)
        proof = controller.verify_execution_projection(expected, actual, baseline, job)
        self.assertTrue(proof['database_alias_verified_against_installed_baseline'])
        expected['env'][0]['value'] = ''
        self.assertTrue(controller.verify_execution_projection(expected, actual, baseline,
            job)['database_alias_verified_against_installed_baseline'])
        case_job = copy.deepcopy(job)
        case_job['identity']['userAssignedIdentities'] = {identity.upper(): {}}
        self.assertTrue(controller.verify_execution_projection(expected, actual, baseline,
            case_job)['database_alias_verified_against_installed_baseline'])
        for path, value in [('secretRef', 'unrelated-secret'), ('value', 'unexpected-value')]:
            changed = copy.deepcopy(actual)
            changed['env'][0][path] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                controller.verify_execution_projection(expected, changed, baseline, job)
        changed = copy.deepcopy(actual)
        changed['resources']['ephemeralStorage'] = '4Gi'
        with self.assertRaises(ValueError):
            controller.verify_execution_projection(expected, changed, baseline, job)
        changed_job = copy.deepcopy(job)
        changed_job['properties']['configuration']['secrets'][0]['keyVaultUrl'] = 'https://other.vault.azure.net/secrets/other'
        with self.assertRaises(ValueError):
            controller.verify_execution_projection(expected, actual, baseline, changed_job)

    def test_missing_workbooks_block_overall_acceptance(self):
        image = 'registry/candidate@sha256:' + '1' * 64
        candidate = {'status': 'passed', 'image': image, 'execution': 'candidate-run'}
        rollback = {'status': 'passed', 'execution': 'fallback-run'}
        for name, value in [('candidate-qualified', candidate), ('rollback-qualified', rollback)]:
            (self.runtime / (name + '.json')).write_text(json.dumps(value))
        audit = self.runtime / 'cloud-local-audit-v1'
        audit.mkdir()
        (audit / 'receipt.json').write_text(json.dumps({'status': 'passed', 'image': image,
            'months_verified': 21, 'runtime_qualified_sha256': controller.build.sha(self.runtime / 'candidate-qualified.json')}))
        workbook_file = self.runtime / 'chart-workbook-acceptance.json'
        workbook_file.write_text(json.dumps({'status': 'blocked', 'image': image,
            'months_checked': 21, 'missing_cloud_chart_workbooks': 126}))
        with patch.object(controller, 'retain', return_value={'protected_readback_verified': True}), \
            contextlib.redirect_stdout(io.StringIO()):
            controller.seal(self.runtime, image)
        result = controller.read(self.runtime, 'programme-acceptance')
        self.assertEqual(result['status'], 'blocked_chart_workbook_acceptance')
        self.assertFalse(result['production_promotion_authorized'])
        self.assertFalse(result['full_live_image_switch_rollback_exercised'])
        self.assertEqual(result['missing_embedded_chart_workbooks'], 126)
        workbook_file.write_text(json.dumps({'status': 'passed', 'image': image,
            'months_checked': 21, 'missing_cloud_chart_workbooks': 126}))
        with patch.object(controller, 'retain') as retain, self.assertRaises(ValueError):
            controller.seal(self.runtime, image)
        retain.assert_not_called()


if __name__ == '__main__':
    unittest.main()
