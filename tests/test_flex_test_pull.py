"""The one-off Flex test pull: one request, raw copy only, nothing secret printed."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import flex_test_pull  # noqa: E402
from ibkr_etl.flex_client import FlexError, FlexReport  # noqa: E402

SECRET = "ibkr-flex-source-" + "a" * 32
TOKEN = "tok-synthetic-123456"
QUERY = "987654"
STATEMENT = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="WealthRyse daily" type="AF">
 <FlexStatements count="1">
  <FlexStatement accountId="U7654321" fromDate="20261005" toDate="20261018">
   <AccountInformation accountId="U7654321" currency="USD" />
   <EquitySummaryInBase>
    <EquitySummaryByReportDateInBase reportDate="20261016" total="100" />
    <EquitySummaryByReportDateInBase reportDate="20261017" total="101" />
   </EquitySummaryInBase>
   <OpenPositions><OpenPosition conid="1" position="1" markPrice="1" positionValue="1" currency="USD" /></OpenPositions>
   <CashReport><CashReportCurrency currency="USD" endingCash="5" /></CashReport>
   <SecuritiesInfo><SecurityInfo conid="1" isin="" /></SecuritiesInfo>
   <ConversionRates><ConversionRate fromCurrency="HKD" rate="0.128" /></ConversionRates>
   <Trades /><CashTransactions /><Transfers /><CorporateActions />
  </FlexStatement>
 </FlexStatements>
</FlexQueryResponse>"""
ENV = {
    "CUTOVER_FLEX_QUERY_ID": QUERY,
    "FLEX_TOKEN_VAULT_URI": "https://example-flex.vault.azure.net",
    "AZURE_CLIENT_ID": "00000000-0000-0000-0000-00000000c11e",
    "AZURE_STORAGE_ACCOUNT_URL": "https://example.blob.core.windows.net",
}
NOON = datetime(2026, 10, 18, 12, 30, tzinfo=timezone.utc)


class _Clock(datetime):
    current = NOON

    @classmethod
    def now(cls, tz=None):
        return cls.current if tz is None else cls.current.astimezone(tz)


def _run(*args: str, now: datetime = NOON, request=None, upload="https://example/blob"):
    output = io.StringIO()
    calls = {"request": 0, "upload": []}

    def fake_request(self, start, end):
        calls["request"] += 1
        calls["window"] = (start, end)
        calls["settings"] = self.settings
        if isinstance(request, Exception):
            raise request
        return FlexReport(STATEMENT, "application/xml", "ref-1")

    def fake_upload(*a, **k):
        calls["upload"].append((a, k))
        return upload

    _Clock.current = now
    with patch.dict(os.environ, ENV, clear=False), \
            patch.object(flex_test_pull, "datetime", _Clock), \
            patch.object(flex_test_pull, "_secret_value", return_value=TOKEN) as secret, \
            patch.object(flex_test_pull.FlexClient, "request_report", fake_request), \
            patch.object(flex_test_pull, "upload_bytes", side_effect=fake_upload), \
            contextlib.redirect_stdout(output):
        code = flex_test_pull.main(["--token-secret-name", SECRET, *args])
    calls["secret"] = secret.call_count
    return code, output.getvalue(), calls


class FlexTestPullTests(unittest.TestCase):
    def test_without_apply_nothing_is_sent(self) -> None:
        code, out, calls = _run()
        self.assertEqual(code, 0)
        self.assertEqual((calls["request"], calls["secret"], calls["upload"]), (0, 0, []))
        self.assertIn("2026-10-04 to 2026-10-17 (14 days)", out)

    def test_refused_while_the_daily_requests_run(self) -> None:
        code, out, calls = _run("--apply", now=datetime(2026, 10, 19, 8, 20, tzinfo=timezone.utc))
        self.assertEqual(code, 2)
        self.assertIn("07:00 and 09:30 UTC", out)
        self.assertEqual((calls["request"], calls["secret"]), (0, 0))

    def test_bad_inputs_are_refused_before_the_vault_is_read(self) -> None:
        output = io.StringIO()
        with patch.dict(os.environ, {**ENV, "CUTOVER_FLEX_QUERY_ID": "abc"}), \
                patch.object(flex_test_pull, "_secret_value") as secret, \
                contextlib.redirect_stdout(output):
            code = flex_test_pull.main(["--token-secret-name", "ibkr-flex-token", "--apply"])
        self.assertEqual(code, 2)
        secret.assert_not_called()
        self.assertIn("ibkr-flex-source-<32 hex>", output.getvalue())
        self.assertIn("not a query ID", output.getvalue())

    def test_one_request_raw_copy_and_recipe_without_secrets_or_accounts(self) -> None:
        code, out, calls = _run("--apply", "--to-date", "2026-10-17")
        self.assertEqual(code, 0, out)
        self.assertEqual(calls["request"], 1)
        self.assertEqual(calls["window"], (date(2026, 10, 4), date(2026, 10, 17)))
        self.assertEqual(calls["settings"].flex_query_id, QUERY)
        (args, kwargs), = calls["upload"]
        self.assertEqual(args[1], "ibkr-raw")
        self.assertTrue(args[2].startswith("test-pulls/"))
        self.assertEqual(args[3], STATEMENT)
        self.assertFalse(kwargs["overwrite"])
        self.assertIn("RESULT: PASSED", out)
        self.assertIn("accounts in statement: 1", out)
        for secret in (TOKEN, QUERY, "U7654321"):
            self.assertNotIn(secret, out)

    def test_ibkr_error_is_reported_once_with_no_retry(self) -> None:
        error = FlexError("Too many requests", code="1018", phase="send_request", lockout=True)
        code, out, calls = _run("--apply", request=error)
        self.assertEqual(code, 3)
        self.assertEqual(calls["request"], 1)
        self.assertEqual(calls["upload"], [])
        self.assertIn("code=1018 lockout=True", out)
        self.assertNotIn(TOKEN, out)

    def test_a_statement_that_cannot_be_saved_fails(self) -> None:
        code, out, _ = _run("--apply", upload=None)
        self.assertEqual(code, 4)
        self.assertIn("could not be saved", out)


if __name__ == "__main__":
    unittest.main()
