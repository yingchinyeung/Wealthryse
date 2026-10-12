"""Review inheritance cannot accept changed current report or page bytes."""
from pathlib import Path
import unittest
from unittest.mock import patch
import carry_ms_unchanged_pdf_review as carry


class UnchangedPdfReviewTests(unittest.TestCase):
    def test_wrong_candidate_family_is_rejected_before_reading(self):
        with self.assertRaisesRegex(ValueError, 'Format-only'):
            carry.evidence(Path('alpha-ms-presentation-worker-candidate-20261007-r5/runtime-v1'))

    def test_canonical_hash_detects_changed_review_fields(self):
        original = {'status':'passed', 'pages_inspected':42}
        changed = {**original, 'pages_inspected':41}
        self.assertNotEqual(carry.canonical_sha(original), carry.canonical_sha(changed))


if __name__ == '__main__':
    unittest.main()
