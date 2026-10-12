"""Reconciliation cannot silently redispatch or accept another candidate."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import build_ms_presentation_worker_candidate as controller


class CandidateBuildControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        (self.folder / 'context').mkdir()
        (self.folder / 'context/Dockerfile').write_text('FROM exact-installed-digest\n')
        self.write('source-intent', {'tag': 'ibkr-platform:scoped-test', 'files_sha256':
            {'Dockerfile': controller.sha(self.folder / 'context/Dockerfile')}})
        source_hash = controller.sha(self.folder / 'source-intent.json')
        self.write('local-qualification', {'status': 'isolated_local_candidate_passed',
            'source_intent_sha256': source_hash, 'replay': {'months_verified': 21}})
        self.write('build-intent', {'tag': 'ibkr-platform:scoped-test',
            'source_intent_sha256': source_hash, 'prepared_at': '2026-10-07T21:43:29+00:00'})
        self.run = {'runId': 'cka6', 'status': 'Succeeded', 'createTime': '2026-10-07T21:43:36Z',
                    'outputImages': [{'registry': controller.REGISTRY + '.azurecr.io',
                       'repository': 'ibkr-platform', 'tag': 'scoped-test', 'digest': 'sha256:' + '4' * 64}]}

    def write(self, name, record):
        (self.folder / (name + '.json')).write_text(json.dumps(record))

    def invoke(self):
        calls = []

        def az(*args):
            calls.append(args)
            if args[:2] == ('account', 'show'):
                return controller.SUB
            if args[:3] == ('acr', 'task', 'show-run'):
                return self.run
            raise AssertionError('Unexpected Azure action: ' + repr(args))

        with patch.object(controller, 'az', side_effect=az), patch('sys.argv',
            ['controller', 'reconcile', '--folder', str(self.folder), '--run-id', 'cka6']), \
            contextlib.redirect_stdout(io.StringIO()):
            controller.main()
        self.assertEqual(len(calls), 2)
        return json.loads((self.folder / 'build-ack.json').read_bytes())

    def test_exact_native_run_is_reconciled_without_build(self):
        self.assertTrue(self.invoke()['reconciled_after_missing_cli_ack'])

    def test_other_image_identities_are_rejected(self):
        for key, value in [('registry', 'other.azurecr.io'), ('repository', 'other'),
                           ('tag', 'other'), ('digest', 'not-a-digest')]:
            with self.subTest(key=key):
                original = self.run['outputImages'][0][key]
                self.run['outputImages'][0][key] = value
                with self.assertRaises(ValueError):
                    self.invoke()
                self.run['outputImages'][0][key] = original
        self.assertFalse((self.folder / 'build-ack.json').exists())

    def test_wrong_run_or_time_or_status_is_rejected(self):
        for key, value in [('runId', 'other'), ('createTime', '2026-10-07T21:46:36Z'),
                           ('createTime', '2026-10-07T21:42:36Z'), ('status', 'Failed')]:
            with self.subTest(key=key, value=value):
                original = self.run[key]
                self.run[key] = value
                with self.assertRaises(ValueError):
                    self.invoke()
                self.run[key] = original

    def test_frozen_context_mutation_is_rejected(self):
        (self.folder / 'context/Dockerfile').write_text('changed')
        with self.assertRaises(ValueError):
            self.invoke()

    def test_reconciliation_cannot_overwrite_existing_ack(self):
        accepted = self.invoke()
        with self.assertRaises(ValueError):
            self.invoke()
        self.assertEqual(json.loads((self.folder / 'build-ack.json').read_bytes()), accepted)


if __name__ == '__main__':
    unittest.main()
