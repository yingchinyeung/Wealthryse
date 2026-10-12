"""Standalone snapshot retries must not supersede reports or bypass quality."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]

class SnapshotEntrypointPreservationTests(unittest.TestCase):
    def load(self):
        spec = importlib.util.spec_from_file_location('snapshot_entrypoint_under_test', ROOT/'scripts/freeze_month_end_snapshot.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def env(self):
        return patch.dict(os.environ, {'DEPLOYMENT_ENVIRONMENT':'staging',
            'DATABASE_URL':'postgresql://synthetic.invalid/example',
            'PILOT_ACCOUNT_ID':'U16088571', 'REPORTING_PERIOD_END':'2026-09-30'})

    def test_existing_snapshot_is_retained_without_writes_or_upload(self):
        m = self.load(); db = MagicMock(); output = io.StringIO()
        with self.env(), patch.object(m, 'connect', return_value=db), \
                patch.object(m, 'active_month_end_snapshot_id', return_value='existing'), \
                patch.object(m, 'freeze_month_end_snapshot') as freeze, \
                patch.object(m, 'upload_bytes') as upload, contextlib.redirect_stdout(output):
            self.assertEqual(m.main(), 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'already_frozen')
        freeze.assert_not_called(); upload.assert_not_called()
        db.commit.assert_not_called(); db.close.assert_called_once()

    def test_new_snapshot_requires_quality_gate(self):
        m = self.load(); db = MagicMock()
        with self.env(), patch.object(m, 'connect', return_value=db), \
                patch.object(m, 'active_month_end_snapshot_id', return_value=None), \
                patch.object(m, 'freeze_month_end_snapshot', side_effect=ValueError('quality')) as freeze, \
                patch.object(m, 'upload_bytes') as upload:
            with self.assertRaisesRegex(ValueError, 'quality'): m.main()
        self.assertIs(freeze.call_args.kwargs['reject_quality_fail'], True)
        upload.assert_not_called(); db.rollback.assert_called_once(); db.close.assert_called_once()

    def test_ambiguous_active_snapshots_abort_before_freeze(self):
        m = self.load(); db = MagicMock()
        with self.env(), patch.object(m, 'connect', return_value=db), \
                patch.object(m, 'active_month_end_snapshot_id', side_effect=ValueError('ambiguous')), \
                patch.object(m, 'freeze_month_end_snapshot') as freeze:
            with self.assertRaisesRegex(ValueError, 'ambiguous'): m.main()
        freeze.assert_not_called(); db.rollback.assert_called_once()

if __name__ == '__main__': unittest.main()
