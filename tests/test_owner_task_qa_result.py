"""Strict QA result parsing; a permitted OS fixture skip is never counted passed."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import verify_owner_task_source_checkpoint as qa


LINK = "test_owner_codex_trial_journal_process.LocalJournalProcessTests.test_symlink_entry_never_followed"
JUNCTION = "test_owner_codex_trial_journal_process.LocalJournalProcessTests.test_actual_windows_junction_entry_is_rejected"
REASON = "OS does not permit unprivileged test symlink creation"
FIFO = "test_owner_codex_trial_journal_process.LocalJournalProcessTests.test_actual_fifo_entry_is_rejected_without_blocking"
DRIVE = "test_owner_codex_trial_journal_process.LocalJournalProcessTests.test_unc_and_mapped_drive_roots_rejected_before_filesystem_access"


def result(text, code=0):
    return SimpleNamespace(stderr=text, returncode=code)


def skipped(test=LINK, reason=REASON, count=1, junction=True):
    text = "test_symlink_entry_never_followed (" + test + ") ... skipped '" + reason + "'\n"
    if junction:
        text += "test_actual_windows_junction_entry_is_rejected (" + JUNCTION + ") ... ok\n"
    return text + "Ran 100 tests in 1.234s\nOK (skipped=" + str(count) + ")\n"


class QaResultTests(unittest.TestCase):
    def test_exact_no_skips_success(self):
        self.assertEqual((100, []), qa.validate_regression_result(result("Ran 100 tests in 1.234s\nOK\n")))

    def test_known_windows_skip_is_not_a_pass(self):
        with patch.object(qa.os, "name", "nt"):
            count, exclusions = qa.validate_regression_result(result(skipped()))
        self.assertEqual(100, count)
        self.assertEqual([{"test": LINK, "reason": REASON, "result": "not_exercised"}], exclusions)

    def test_two_known_windows_fixture_limits_are_recorded_separately(self):
        text = skipped().replace("Ran 100", "test_actual_fifo_entry_is_rejected_without_blocking (" + FIFO +
            ") ... skipped 'POSIX FIFO fixture'\nRan 100").replace("skipped=1", "skipped=2")
        with patch.object(qa.os, "name", "nt"):
            count, exclusions = qa.validate_regression_result(result(text))
        self.assertEqual(100, count)
        self.assertEqual(2, len(exclusions))
        self.assertTrue(all(e["result"] == "not_exercised" for e in exclusions))

    def test_windows_skip_requires_actual_junction_negative(self):
        with patch.object(qa.os, "name", "nt"):
            with self.assertRaisesRegex(ValueError, "^Actual Windows junction rejection was not exercised$"):
                qa.validate_regression_result(result(skipped(junction=False)))

    def test_unknown_skip_or_reason_blocks(self):
        with patch.object(qa.os, "name", "nt"):
            for test, reason in (("test_security.Test.test_critical", REASON), (LINK, "unexpected permission error")):
                with self.assertRaises(ValueError):
                    qa.validate_regression_result(result(skipped(test, reason)))

    def test_missing_duplicate_mismatched_summary_and_failure_block(self):
        values = ("Ran 0 tests in 1.2s\nOK\n", "Ran 100 tests in 1.2s\n",
            "Ran 100 tests in 1.2s\nOK\nOK\n", "Ran 100 tests in 1.2s\nOK (expected failures=1)\n",
            "Ran 100 tests in 1.2s\nOK (skipped=1)\n", skipped(count=2),
            skipped() + "Ran 100 tests in 1.2s\n", "Ran 100 tests in 1.2s\nFAILED (errors=1)\nOK\n")
        for value in values:
            with self.assertRaises(ValueError):
                qa.validate_regression_result(result(value))
        with self.assertRaises(ValueError):
            qa.validate_regression_result(result("Ran 100 tests in 1.2s\nOK\n", code=1))

    def test_posix_fixture_skip_is_explicit_not_windows_symlink_exception(self):
        text = "test_actual_windows_junction_entry_is_rejected (" + JUNCTION + ") ... skipped 'Windows junction fixture'\nRan 100 tests in 1.2s\nOK (skipped=1)\n"
        with patch.object(qa.os, "name", "posix"):
            count, exclusions = qa.validate_regression_result(result(text))
            self.assertEqual(100, count)
            self.assertEqual("not_exercised", exclusions[0]["result"])
            with self.assertRaises(ValueError):
                qa.validate_regression_result(result(skipped()))

    def test_posix_records_both_windows_only_fixtures_without_counting_passes(self):
        text = ("test_actual_windows_junction_entry_is_rejected (" + JUNCTION +
            ") ... skipped 'Windows junction fixture'\n"
            "test_unc_and_mapped_drive_roots_rejected_before_filesystem_access (" + DRIVE +
            ") ... skipped 'Windows drive metadata fixture'\nRan 100 tests in 1.2s\nOK (skipped=2)\n")
        with patch.object(qa.os, "name", "posix"):
            count, exclusions = qa.validate_regression_result(result(text))
        self.assertEqual(100, count)
        self.assertEqual([JUNCTION, DRIVE], [entry["test"] for entry in exclusions])
        self.assertTrue(all(entry["result"] == "not_exercised" for entry in exclusions))
        with patch.object(qa.os, "name", "nt"):
            with self.assertRaises(ValueError):
                qa.validate_regression_result(result(text))


if __name__ == "__main__":
    unittest.main()
