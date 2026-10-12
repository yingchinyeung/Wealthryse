from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_metadata_monitor_watchdog import query


class MetadataMonitorWatchdogTests(unittest.TestCase):
    def test_requires_verified_completion(self):
        text = query()
        self.assertIn("Evidence.kind == 'complete'", text)
        self.assertIn("Evidence.access_check == 'passed'", text)
        self.assertIn("Evidence.mode == 'shadow'", text)
        self.assertIn("substring(Log_s, " + str(len("METADATA_MONITOR ")) + ")", text)

    def test_empty_stream_and_delayed_completion_detected(self):
        text = query()
        self.assertIn("summarize LastCompletion=max(TimeGenerated)", text)
        self.assertIn("isnull(LastCompletion) or LastCompletion < ago(75m)", text)

    def test_not_a_financial_finding_alert(self):
        text = query()
        self.assertNotIn("needs_review", text)
        self.assertNotIn("risk_behind_nav", text)
        self.assertIn("ibkrv2stg-metadata-monitor-", text)


if __name__ == "__main__":
    unittest.main()
