"""Source/image/execution acceptance must not imply production capability."""
import copy
import contextlib
import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import prepare_codex_exec_probe as prepare
import deploy_codex_exec_probe as deploy
import verify_codex_exec_checkpoint as checkpoint


def result():
    return dict(schema='codex-synthetic-exec-acceptance-v1', status='passed', mode='azure',
                runtime_version='0.160.0', request_count=1, accepted_count=1,
                source_data_sent=False, upstream_tools_enabled=False, relay_failure=None,
                interface='cli-exec', interface_maturity='stable', owner_runtime_active=False,
                six_specialist_runtimes_active=False, production_accepted=False,
                discarded_declaration_count=1, discarded_declaration_sha256=deploy.DECLARATION_TOOLS_SHA256,
                declaration_tools_forwarded=False,
                **{k: True for k in deploy.CHECKS})


class CodexExecDeploymentTests(unittest.TestCase):
    def test_checkpoint_fingerprint_uses_exact_protected_settings_projection(self):
        fixture = dict(identity={'registered': {}}, properties=dict(configuration={'retry': 0},
            template={'pinned': 'image'}, outboundIpAddresses=['metadata'], provisioningState='Succeeded'))
        expected = deploy.fingerprint(dict(identity=fixture['identity'],
            configuration=fixture['properties']['configuration'], template=fixture['properties']['template']))
        self.assertEqual(checkpoint.daily_fingerprint(fixture), expected)
        altered = copy.deepcopy(fixture); altered['properties']['outboundIpAddresses'] = ['different']
        self.assertEqual(checkpoint.daily_fingerprint(altered), expected)
        altered['properties']['configuration']['retry'] = 1
        self.assertNotEqual(checkpoint.daily_fingerprint(altered), expected)

    def archive_fixture(self):
        value = dict(status='intent', synthetic_only=True)
        raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
        receipt = dict(blob='acceptance/gateway-read-codex-exec-build-intent-'+'a'*64+'.json',
            version_id='2026-10-03T03:46:03.9691046Z', sha256='a'*64, protected_readback_verified=True)
        blob = Mock()
        blob.get_blob_properties.return_value = SimpleNamespace(version_id=receipt['version_id'], size=len(raw), etag='fixed')
        blob.download_blob.return_value.readall.return_value = raw
        service = Mock(); service.get_blob_client.return_value = blob
        return dict(value, protected_receipt=receipt), raw, blob, service

    def consume_fixture(self, value, service):
        with patch.object(deploy, 'local_protected_record', return_value=value), \
             patch.object(deploy, 'BlobServiceClient', return_value=service), \
             patch.object(deploy, 'AzureCliCredential'), \
             patch.object(deploy, 'request', return_value={'id': deploy.SCOPE, 'properties': {'hasLegalHold': True}}), \
             patch.object(deploy, 'validate_archive_properties') as protection:
            result = deploy.protected_record(Path('fixture'), 'intent')
            self.assertEqual(protection.call_count, 2)
            return result

    def test_remote_receipt_requires_exact_version_bytes_and_conditional_read(self):
        value, raw, blob, service = self.archive_fixture()
        self.assertEqual(self.consume_fixture(value, service), value)
        service.get_blob_client.assert_called_once_with(deploy.CONTAINER, value['protected_receipt']['blob'],
            version_id=value['protected_receipt']['version_id'])
        blob.download_blob.assert_called_once_with(offset=0, length=len(raw)+1, etag='fixed',
            match_condition=deploy.MatchConditions.IfNotModified)

    def test_remote_receipt_does_not_trust_local_byte_assertion(self):
        value, _, blob, service = self.archive_fixture()
        blob.download_blob.return_value.readall.return_value = b'different'
        with self.assertRaisesRegex(ValueError, 'remote bytes differ'):
            self.consume_fixture(value, service)

    def test_remote_receipt_rejects_wrong_version_or_size(self):
        for property_name, changed in [('version_id', 'different'), ('size', 0)]:
            value, _, blob, service = self.archive_fixture()
            setattr(blob.get_blob_properties.return_value, property_name, changed)
            with self.assertRaisesRegex(ValueError, 'archive version differs'):
                self.consume_fixture(value, service)
            blob.download_blob.assert_not_called()

    def test_remote_receipt_rejects_other_namespace_and_unversioned_read(self):
        for key, changed in [('blob', 'acceptance/other-'+ 'a'*64+'.json'), ('version_id', '')]:
            value, _, _, service = self.archive_fixture()
            value['protected_receipt'][key] = changed
            with self.assertRaisesRegex(ValueError, 'Unregistered'):
                self.consume_fixture(value, service)
            service.get_blob_client.assert_not_called()

    def test_remote_protection_requires_exact_arm_scope_and_boolean_hold(self):
        for value in ({'id': 'different', 'properties': {'hasLegalHold': True}},
                      {'id': deploy.SCOPE, 'properties': {'hasLegalHold': 'true'}}):
            with patch.object(deploy, 'request', return_value=value), \
                 patch.object(deploy, 'validate_archive_properties') as imported:
                with self.assertRaisesRegex(ValueError, 'identity or hold differs'):
                    deploy.archive_protection()
                imported.assert_not_called()

    def test_result_is_synthetic_only(self):
        deploy.validate_result(result())
        for key, value in [('request_count', True), ('accepted_count', 2),
                           ('production_accepted', True), ('owner_runtime_active', True),
                           ('six_specialist_runtimes_active', True), ('interface', 'app-server'),
                           ('source_data_sent', True), ('relay_failure', 'failed'),
                           ('discarded_declaration_count', True), ('discarded_declaration_count', 2),
                           ('discarded_declaration_sha256', 'different'), ('declaration_tools_forwarded', True),
                           ('runtime_version', 'rolling')]:
            changed = result(); changed[key] = value
            with self.assertRaises(ValueError):
                deploy.validate_result(changed)
        for key in deploy.CHECKS:
            changed = result(); changed[key] = False
            with self.assertRaises(ValueError):
                deploy.validate_result(changed)

    def test_execution_scope_is_exact(self):
        target = dict(containers=[dict(name='probe', image='pinned', resources=dict(cpu=1, memory='2Gi'),
                      env=[dict(name='AZURE_CLIENT_ID', value='registered')], command=['python'],
                      args=['scripts/run_codex_exec_probe.py', '--mode', 'azure'])])
        deploy.validate_execution_template(target, target)
        observed = copy.deepcopy(target)
        observed['containers'][0]['resources']['ephemeralStorage'] = ''
        deploy.validate_execution_template(observed, target)
        for key, value in [('cpu', True), ('cpu', 2), ('memory', '1Gi'), ('ephemeralStorage', '1Gi')]:
            altered = copy.deepcopy(target); altered['containers'][0]['resources'][key] = value
            with self.assertRaises(ValueError):
                deploy.validate_execution_template(altered, target)
        for key, value in [('env', []), ('args', ['different']), ('volumeMounts', ['source'])]:
            altered = copy.deepcopy(target); altered['containers'][0][key] = value
            with self.assertRaises(ValueError):
                deploy.validate_execution_template(altered, target)

    def test_frozen_context_contains_only_eight_reviewed_files(self):
        files, record = prepare.manifest()
        self.assertEqual(len(files), 8)
        self.assertEqual(set(files), set(prepare.PATHS))
        self.assertEqual(set(record['helpers']), set(prepare.HELPERS))
        self.assertIs(record['portfolio_data_included'], False)
        self.assertIs(record['credentials_included'], False)
        self.assertIn('codex_exec_probe.py', ''.join(files))
        self.assertTrue(all(not any(term in name.lower() for term in ('factsheet', 'flex', '.env', 'database')) for name in files))
        docker = files['Dockerfile'].decode()
        self.assertIn('USER 10001:10001', docker)
        self.assertIn('RUN python scripts/run_codex_exec_probe.py --mode offline', docker)
        self.assertIn('@sha256:', docker.splitlines()[0])

    def test_prepare_is_create_only_and_rejects_mutated_context(self):
        with tempfile.TemporaryDirectory() as name, patch.object(prepare, 'FOLDER', Path(name)):
            with contextlib.redirect_stdout(io.StringIO()):
                prepare.main(); prepare.main()
            _, record = prepare.manifest()
            context = Path(name)/record['source_manifest_sha256'][:12]/'context'
            (context/'unexpected').write_text('not accepted')
            with self.assertRaises(ValueError):
                prepare.main()

    def test_build_names_exact_frozen_dockerfile(self):
        argv = deploy.build_command(Path('synthetic-frozen-context'), 'fixed')
        self.assertEqual(Path(argv[argv.index('--file')+1]), (Path('synthetic-frozen-context')/'Dockerfile').resolve())
        self.assertIn('--no-wait', argv)
        self.assertIn('--no-logs', argv)

    def test_runner_verifies_cli_not_experimental_app_server(self):
        value = (ROOT/'scripts/run_codex_exec_probe.py').read_text()
        self.assertIn('cli_flags_verified', value)
        self.assertIn('process_exit_verified', value)
        self.assertNotIn('generate-json-schema', value)
        self.assertIn('production_accepted=False', value)
        self.assertIn("'CODEX_EXEC_PROBE '", value)


if __name__ == '__main__':
    unittest.main()
