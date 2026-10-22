from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import store_ibkr_login as store  # noqa: E402


class StoreLoginTests(unittest.TestCase):
    def run_store(self, existing, answers, argv=()):
        calls = []

        def request(method, url, *, data=None, missing=False, **_):
            calls.append((method, url, data))
            if method == "GET":
                return {"id": url} if any("/secrets/" + n + "?" in url for n in existing) else None
            return {}

        replies = iter(answers)
        out = io.StringIO()
        with mock.patch.object(store.stage1, "request", side_effect=request), contextlib.redirect_stdout(out):
            store.main(list(argv), prompt=lambda _: next(replies))
        return calls, out.getvalue()

    def test_writes_both_through_resource_manager_without_printing_them(self):
        calls, out = self.run_store([], ["trader1", "trader1", "s3cret!", "s3cret!"])
        puts = [(url, data) for method, url, data in calls if method == "PUT"]
        self.assertEqual([url.split("/secrets/")[1].split("?")[0] for url, _ in puts], ["ibkr-username", "ibkr-password"])
        self.assertTrue(all(url.startswith("/subscriptions/") and "vault.azure.net" not in url for url, _ in puts))
        self.assertEqual([data["properties"]["value"] for _, data in puts], ["trader1", "s3cret!"])
        self.assertNotIn("trader1", out)
        self.assertNotIn("s3cret!", out)

    def test_existing_secrets_are_left_alone_without_replace(self):
        calls, out = self.run_store(["ibkr-username", "ibkr-password"], [])
        self.assertFalse([c for c in calls if c[0] == "PUT"])
        self.assertIn("left as it is", out)

    def test_replace_overwrites_an_existing_secret(self):
        calls, out = self.run_store(["ibkr-username"], ["u", "u", "p", "p"], argv=["--replace"])
        self.assertEqual(len([c for c in calls if c[0] == "PUT"]), 2)
        self.assertIn("replaced", out)

    def test_mismatched_confirmation_writes_nothing_for_that_secret(self):
        with self.assertRaises(SystemExit):
            calls, _ = self.run_store([], ["u", "x"])

    def test_empty_value_is_refused(self):
        with self.assertRaises(SystemExit):
            self.run_store([], [""])


if __name__ == "__main__":
    unittest.main()
