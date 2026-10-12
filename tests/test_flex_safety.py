from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from ibkr_etl.config import Settings
from ibkr_etl.flex_client import FlexClient, FlexError, is_lockout_error
from ibkr_etl.flex_guard import FlexCircuitOpen, circuit_status, guarded_flex_request


def _settings(root: Path) -> Settings:
    return Settings(
        project_root=root,
        flex_token="not-a-real-token",
        flex_query_id="1609397",
        database_path=root / "portfolio.db",
        raw_report_dir=root / "raw",
        log_dir=root / "logs",
        timezone_name="Asia/Hong_Kong",
        daily_lookback_days=7,
        user_agent="test",
        request_timeout_seconds=10,
        max_wait_seconds=60,
        flex_min_send_interval_seconds=10,
        flex_transient_cooldown_seconds=1800,
    )


class FlexClientSafetyTests(unittest.TestCase):
    def test_pending_statement_polls_same_reference_without_second_send(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            client = FlexClient(_settings(Path(temporary)))
            send = b"<FlexStatementResponse><Status>Success</Status><ReferenceCode>ABC123</ReferenceCode></FlexStatementResponse>"
            pending = b"<FlexStatementResponse><Status>Fail</Status><ErrorCode>1019</ErrorCode><ErrorMessage>Statement generation in progress</ErrorMessage></FlexStatementResponse>"
            complete = b"<FlexQueryResponse />"
            with patch.object(
                client,
                "_get",
                side_effect=[
                    (send, "application/xml"),
                    (pending, "application/xml"),
                    (complete, "application/xml"),
                ],
            ) as get, patch("ibkr_etl.flex_client.time.sleep"):
                result = client.request_report(date(2026, 8, 31), date(2026, 8, 31))

            self.assertEqual(result.reference_code, "ABC123")
            paths = [call.args[0] for call in get.call_args_list]
            self.assertEqual(paths.count("/SendRequest"), 1)
            self.assertEqual(paths.count("/GetStatement"), 2)
            self.assertTrue(all(call.args[1]["q"] == "ABC123" for call in get.call_args_list[1:]))

    def test_send_failure_is_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            client = FlexClient(_settings(Path(temporary)))
            failed = b"<FlexStatementResponse><Status>Fail</Status><ErrorCode>1001</ErrorCode><ErrorMessage>Statement could not be generated</ErrorMessage></FlexStatementResponse>"
            with patch.object(client, "_get", return_value=(failed, "application/xml")) as get:
                with self.assertRaises(FlexError) as caught:
                    client.request_report(date(2026, 8, 31), date(2026, 8, 31))
            self.assertEqual(caught.exception.phase, "send_request")
            self.assertEqual(get.call_count, 1)

    def test_lockout_error_fails_closed_without_polling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            client = FlexClient(_settings(Path(temporary)))
            failed = b"<FlexStatementResponse><Status>Fail</Status><ErrorCode>1018</ErrorCode><ErrorMessage>Too many failed attempts</ErrorMessage></FlexStatementResponse>"
            with patch.object(client, "_get", return_value=(failed, "application/xml")) as get:
                with self.assertRaises(FlexError) as caught:
                    client.request_report(date(2026, 8, 31), date(2026, 8, 31))
            self.assertTrue(caught.exception.lockout)
            self.assertEqual(get.call_count, 1)

    def test_unknown_lockout_phrase_is_recognized(self) -> None:
        self.assertTrue(is_lockout_error(None, "Your credentials are temporarily locked"))


class FlexCircuitTests(unittest.TestCase):
    def test_postgres_upsert_names_the_tenant_primary_key(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "ibkr_etl"
            / "flex_guard.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "ON CONFLICT (tenant_id, token_fingerprint, query_id) DO UPDATE SET",
            source,
        )
        self.assertIn("connection.rollback()", source)

    def test_lockout_persists_and_blocks_the_next_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = _settings(Path(temporary))
            with self.assertRaises(FlexError):
                with guarded_flex_request(settings):
                    raise FlexError(
                        "Too many failed attempts",
                        code="1018",
                        phase="send_request",
                        lockout=True,
                    )

            state = circuit_status(settings)
            self.assertEqual(state["requires_manual_reset"], 1)
            self.assertEqual(state["last_error_phase"], "send_request")
            self.assertEqual(state["last_error_code"], "1018")
            with self.assertRaises(FlexCircuitOpen):
                with guarded_flex_request(settings):
                    self.fail("circuit should prevent network work")

    def test_success_clears_transient_state_but_preserves_send_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = _settings(Path(temporary))
            with guarded_flex_request(settings):
                pass
            state = circuit_status(settings)
            self.assertEqual(state["consecutive_failures"], 0)
            self.assertIsNotNone(state["last_send_at"])
            with self.assertRaises(FlexCircuitOpen):
                with guarded_flex_request(settings):
                    self.fail("minimum interval should block a second immediate send")


if __name__ == "__main__":
    unittest.main()
