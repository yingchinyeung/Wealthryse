"""Checks run against the actual frozen overlay and inherited report runtime."""
import hashlib
import json
from pathlib import Path
import unittest


class MsPresentationWorkerRuntimeTests(unittest.TestCase):
    def test_installed_source_files_match_frozen_release(self):
        root = Path(__file__).resolve().parents[1]
        manifest = root / 'MS_PRESENTATION_WORKER_RELEASE.json'
        if not manifest.exists():
            self.skipTest('Executed only in the isolated candidate or installed candidate image')
        release = json.loads(manifest.read_bytes())
        self.assertTrue(release['base_image'].endswith('@sha256:d98e16415d7e4b601571913f2d3be3bc48e62c69d9725419f556a2b71333a584'))
        self.assertFalse(release['report_design_updates_included'])
        self.assertFalse(release['source_data_changed'])
        self.assertFalse(release['report_release_authorized'])
        for name, digest in release['installed_files_sha256'].items():
            with self.subTest(source=name):
                self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)
        monthly = (root / 'scripts/generate_month_end_package.py').read_text(encoding='utf-8')
        for excluded in ('report_design_updates', 'REPORT_DESIGN_UPDATES_ENABLED',
                         'qualified_report_designs', '_pending_design_update', 'allow_replacement'):
            self.assertNotIn(excluded, monthly)
        self.assertIn('upload_ms_review', monthly)
