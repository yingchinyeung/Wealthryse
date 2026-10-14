"""The connection agent's rules. Synthetic values only."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from wealthryse_agent.connection_watch import eligible, findings, problem, watch_connections

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def login(**overrides):
    row = {"connection_id": "c1", "display_name": "Synthetic login", "connection_status": "pending_check",
           "flex_source_id": "s1", "source_status": "pending_validation", "credential_status": "pending_validation",
           "blocked_until": None, "requires_manual_reset": False, "checks_requested": 0, "latest_request_id": None,
           "latest_status": None, "latest_error_phase": None, "latest_error_code": None, "latest_created_at": None,
           "latest_account_count": None, "accounts_found": 0}
    row.update(overrides)
    return row


class FakeStore:
    def __init__(self, queued="r1"):
        self.queued, self.asked, self.reports = queued, [], {}

    def queue_first_connection_check(self, flex_source_id):
        self.asked.append(flex_source_id)
        return self.queued

    def add_connection_report(self, *, connection_id, flex_source_id, key, state, title, body, owner_steps,
                              validation_request_id, run_id):
        if (flex_source_id, key) in self.reports:
            return False
        self.reports[(flex_source_id, key)] = (state, title, body, owner_steps, validation_request_id)
        return True


class ConnectionAgentTests(unittest.TestCase):
    def test_only_a_never_checked_login_gets_a_first_check(self):
        self.assertTrue(eligible(login(), NOW))
        for change in ({"checks_requested": 1}, {"source_status": "validated"}, {"credential_status": "revoked"},
                       {"connection_status": "suspended"}, {"requires_manual_reset": True},
                       {"blocked_until": "2026-10-14T12:30:00+00:00"}):
            with self.subTest(change=change):
                self.assertFalse(eligible(login(**change), NOW))
        self.assertTrue(eligible(login(blocked_until="2026-10-14T11:00:00+00:00"), NOW))

    def test_queueing_is_reported_once_and_a_refused_queue_says_nothing(self):
        store = FakeStore()
        logins = [login()]
        call = lambda name, arguments: {"data": {"logins": logins}}  # noqa: E731
        self.assertEqual(watch_connections(store, call, None, NOW), 1)
        self.assertEqual(store.asked, ["s1"])
        (state, title, _, _, request), = store.reports.values()
        self.assertEqual((state, request), ("queued", "r1"))
        self.assertIn("checking the new IBKR login", title)
        self.assertEqual(watch_connections(FakeStore(queued=None), call, None, NOW), 0)

    def test_each_result_says_exactly_what_happened(self):
        done = findings(login(latest_request_id="r1", latest_status="succeeded", latest_account_count=3), NOW)
        self.assertEqual(done[0]["state"], "found_accounts")
        self.assertIn("sees 3 accounts", done[0]["title"])
        failed = findings(login(latest_request_id="r1", latest_status="blocked", latest_error_code="1013",
                                latest_error_phase="send_request", requires_manual_reset=True), NOW)[0]
        self.assertIn("IP-locked to a different address (IBKR error 1013)", failed["body"])
        self.assertIn("will not use this token again", failed["body"])
        self.assertTrue(any("IP restriction" in step for step in failed["owner_steps"]))
        stuck = findings(login(latest_request_id="r1", latest_status="pending",
                               latest_created_at="2026-10-14T11:00:00+00:00"), NOW)[0]
        self.assertEqual(stuck["state"], "stuck")
        self.assertIn("pending for 60 minutes", stuck["body"])
        self.assertEqual(findings(login(latest_request_id="r1", latest_status="pending",
                                        latest_created_at="2026-10-14T11:50:00+00:00"), NOW), [])

    def test_every_recorded_failure_has_plain_words(self):
        for code, phase, words in (
            ("1012", "send_request", "the token has expired"),
            ("1019", "get_statement", "still preparing the statement"),
            ("flex_error", "send_request", "could not get an answer from IBKR"),
            ("credential_manual_reset_required", "preflight", "held after IBKR refused it"),
            ("invalid_discovered_account_count", "validation", "had no accounts in it"),
            ("Flex token secret is empty", "validation", "could not read the token from Key Vault"),
            ("something new", "validation", "failed in its validation step (code something new)"),
        ):
            with self.subTest(code=code):
                self.assertIn(words, problem(code, phase)[0])


if __name__ == "__main__":
    unittest.main()
