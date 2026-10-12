import copy
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

import run_ms_installed_private_batch as batch
import deploy_ms_presentation_worker_release as release


class InstalledBatchTests(unittest.TestCase):
    def test_format_installed_batch_does_not_reuse_old_private_prefix(self):
        prefix = batch.installed_prefix(Path('alpha-ms-format-worker-candidate-20261008-r4'))
        self.assertEqual(prefix, 'review/alpha-world-ms/format-worker-r4-20261008/installed-private-batch/')
        self.assertNotEqual(prefix, batch.installed_prefix(Path('alpha-ms-presentation-worker-candidate-20261007-r5')))
        with self.assertRaises(ValueError):
            batch.installed_prefix(Path('alpha-ms-format-worker-candidate-20261308-r4'))

    def fixture(self):
        root = release.build.ROOT / '.work/alpha-ms-presentation-worker-candidate-20261007-r5'
        prior = release.read(root / 'runtime-v1', 'candidate-qualified')
        proof = copy.deepcopy(prior['proof'])
        for row in proof['artifacts']:
            for kind in ('factsheet', 'presentation'):
                row[kind + '_blob'] = batch.PREFIX + 'render/outputs/' + Path(row[kind + '_blob']).name
        return proof, {'image': prior['image']}, prior

    def test_accepted_inputs_and_output_contract(self):
        batch.validate_proof(*self.fixture())

    def test_scope_and_coverage_fail_closed(self):
        for field, value in [('database_writes', 1), ('source_data_changed', True),
                             ('reports_released', True), ('onboarding_activated', True),
                             ('read_only_transaction_enforced', False), ('count', 20), ('image', 'other')]:
            with self.subTest(field=field):
                proof, intent, prior = self.fixture(); proof[field] = value
                with self.assertRaises(ValueError): batch.validate_proof(proof, intent, prior)

    def test_identity_pdf_and_financial_mutations_fail_closed(self):
        for field, value in [('snapshot_id', 'other'), ('data_sha256', '0'*64),
                             ('factsheet_sha256', '0'*64), ('financial_charts_verified', 5),
                             ('risk_metrics_verified', 6), ('embedded_workbooks_verified', 0),
                             ('factsheet_blob', 'published/other.pdf')]:
            with self.subTest(field=field):
                proof, intent, prior = self.fixture(); proof['artifacts'][0][field] = value
                with self.assertRaises(ValueError): batch.validate_proof(proof, intent, prior)

    def test_duplicate_month_fails_closed(self):
        proof, intent, prior = self.fixture(); proof['artifacts'][0] = proof['artifacts'][1]
        with self.assertRaises(ValueError): batch.validate_proof(proof, intent, prior)

    def test_package_comparison_checks_every_member(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.pptx'
            with ZipFile(path, 'w') as archive: archive.writestr('ppt/slides/slide1.xml', 'approved')
            first = batch.package_parts(path)
            with ZipFile(path, 'w') as archive: archive.writestr('ppt/slides/slide1.xml', 'changed')
            self.assertNotEqual(first, batch.package_parts(path))


if __name__ == '__main__': unittest.main()
