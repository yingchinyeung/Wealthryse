"""The account move's inputs from the flex vault: query ID and token fingerprint, never printed."""

from __future__ import annotations

import contextlib
import hashlib
import io
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import cutover_v11_accounts_to_flex as cutover  # noqa: E402

TOKEN_SECRET = "ibkr-flex-source-" + "a" * 32
QUERY_SECRET = "ibkr-flex-query-" + "a" * 32
TOKEN = "tok-synthetic-123456"
QUERY = "987654"
ENV = {"FLEX_TOKEN_VAULT_URI": "https://example-flex.vault.azure.net",
       "AZURE_CLIENT_ID": "00000000-0000-0000-0000-00000000c11e"}


def _arguments(*extra: str):
    return cutover._arguments(["--effective-date", "2026-10-18", "--token-secret-name", TOKEN_SECRET,
                               "--statement", "v11.xml", *extra])


class VaultInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reads = []

    def _secret(self, vault, name, client):
        self.reads.append(name)
        return {QUERY_SECRET: QUERY + "\n", TOKEN_SECRET: TOKEN}[name]

    def test_without_vault_options_the_environment_is_used_and_the_vault_is_not_read(self) -> None:
        query, fingerprint = cutover.vault_inputs(_arguments(), {"CUTOVER_FLEX_QUERY_ID": " 123 "}, self._secret)
        self.assertEqual((query, fingerprint, self.reads), ("123", None, []))

    def test_query_id_and_fingerprint_come_from_the_vault(self) -> None:
        args = _arguments("--query-id-secret-name", QUERY_SECRET, "--token-fingerprint-from-vault")
        query, fingerprint = cutover.vault_inputs(args, ENV, self._secret)
        self.assertEqual(query, QUERY)
        self.assertEqual(fingerprint, hashlib.sha256(TOKEN.encode()).hexdigest()[:16])
        self.assertEqual(self.reads, [QUERY_SECRET, TOKEN_SECRET])

    def test_vault_options_need_the_vault_settings(self) -> None:
        args = _arguments("--query-id-secret-name", QUERY_SECRET)
        with self.assertRaisesRegex(RuntimeError, "FLEX_TOKEN_VAULT_URI"):
            cutover.vault_inputs(args, {}, self._secret)
        self.assertEqual(self.reads, [])

    def test_bad_names_and_two_fingerprints_are_refused(self) -> None:
        for extra in (("--query-id-secret-name", "ibkr-flex-query-id"),
                      ("--token-fingerprint", "0" * 16, "--token-fingerprint-from-vault")):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                _arguments(*extra)

    def test_the_effective_date_is_still_parsed(self) -> None:
        self.assertEqual(_arguments().effective_date, date(2026, 10, 18))


if __name__ == "__main__":
    unittest.main()
