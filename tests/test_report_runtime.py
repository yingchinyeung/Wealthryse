from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_report_runtime import check_templates


class ReportRuntimeTests(unittest.TestCase):
    def test_missing_template_stops_release(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'Report runtime is incomplete'):
                check_templates(Path(directory))

    def test_corrupt_template_stops_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'templates').mkdir()
            (root / 'templates' / 'bad.json').write_text('invalid', encoding='utf-8')
            with patch('scripts.check_report_runtime.TEMPLATES', ('bad.json',)):
                with self.assertRaises(ValueError):
                    check_templates(root)

    def test_container_build_enforces_runtime_and_locked_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        docker = (root / 'Dockerfile').read_text()
        self.assertIn('RUN python scripts/check_report_runtime.py', docker)
        self.assertIn('--require-hashes -r requirements.lock', docker)
        self.assertIn('nodejs npm', docker)
        self.assertIn('approved-august-2026-20-slide-v24.pptx', docker)


if __name__ == '__main__':
    unittest.main()
