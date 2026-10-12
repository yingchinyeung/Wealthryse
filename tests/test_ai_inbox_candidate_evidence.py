import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from verify_ai_inbox_candidate import test_summary


class BuildEvidenceTests(unittest.TestCase):
    def test_linux_skips_are_not_claimed_as_executed_passes(self):
        self.assertEqual(test_summary('Ran 1123 tests in 9.2s\n\nOK (skipped=7)\n'),(1123,'OK (skipped=7)',7))

    def test_failed_build_preserves_skipped_count(self):
        self.assertEqual(test_summary('Ran 1122 tests in 9.2s\n\nFAILED (failures=1, errors=6, skipped=7)\n'),
                         (1122,'FAILED (failures=1, errors=6, skipped=7)',7))

    def test_missing_or_duplicate_test_evidence_rejected(self):
        for text in ('no tests recorded','Ran 1 tests in 1s\nOK\nRan 1 tests in 1s\nOK\n'):
            with self.assertRaises(ValueError):test_summary(text)


if __name__=='__main__':unittest.main()
