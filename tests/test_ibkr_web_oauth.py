"""IBKR Web API OAuth 1.0a client and daily-bar reader, against a fake IBKR server."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import unittest
import urllib.parse
from datetime import date
from decimal import Decimal

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ibkr_etl import ibkr_web_market_data as md
from ibkr_etl import ibkr_web_oauth as oauth

# RFC 3526 group 14 (2048-bit MODP) prime: a valid DH prime that needs no generation.
PRIME = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF", 16)


def _pem_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return key, private


class FakeIbkr:
    """Plays IBKR's side: checks every signature and answers like the Web API."""

    def __init__(self):
        self.signature_key, self.signature_pem = _pem_pair()
        self.encryption_key, self.encryption_pem = _pem_pair()
        self.secret = secrets.token_bytes(32)
        self.access_token_secret = base64.b64encode(
            self.encryption_key.public_key().encrypt(self.secret, padding.PKCS1v15())).decode()
        self.lst: bytes | None = None
        self.calls: list[tuple[str, str]] = []
        self.history_answers: list[dict] = []

    def credentials(self, consumer_key="WEALTHRYS"):
        return oauth.OAuthCredentials(consumer_key, "accesstoken123", self.access_token_secret,
                                      self.signature_pem, self.encryption_pem, PRIME)

    @staticmethod
    def _params(header: str) -> dict[str, str]:
        assert header.startswith('OAuth realm="limited_poa", ')
        body = header[len('OAuth realm="limited_poa", '):]
        return {k: v.strip('"') for k, v in (part.split("=", 1) for part in body.split(", "))}

    def __call__(self, method, url, headers, body):
        split = urllib.parse.urlsplit(url)
        bare = f"{split.scheme}://{split.netloc}{split.path}"
        path = split.path.replace("/v1/api", "", 1)
        self.calls.append((method, path))
        params = self._params(headers["Authorization"])
        signature = urllib.parse.unquote_plus(params.pop("oauth_signature"))
        if path == oauth.LIVE_SESSION_TOKEN_PATH:
            prepend = self.secret.hex()
            text = oauth.base_string(method, bare, params, prepend)
            self.signature_key.public_key().verify(base64.b64decode(signature), text.encode(),
                                                   padding.PKCS1v15(), hashes.SHA256())
            b = secrets.randbits(256)
            shared = pow(int(params["diffie_hellman_challenge"], 16), b, PRIME)
            self.lst = hmac.new(oauth.java_byte_array(shared), self.secret, hashlib.sha1).digest()
            return oauth.Response(200, json.dumps({
                "diffie_hellman_response": format(pow(2, b, PRIME), "x"),
                "live_session_token_signature": hmac.new(self.lst, params["oauth_consumer_key"].encode(),
                                                         hashlib.sha1).hexdigest(),
                "live_session_token_expiration": 4_102_444_800_000,
            }).encode())
        query = dict(urllib.parse.parse_qsl(split.query))
        text = oauth.base_string(method, bare, {**params, **query})
        expected = base64.b64encode(hmac.new(self.lst, text.encode(), hashlib.sha256).digest()).decode()
        if not hmac.compare_digest(expected, signature):
            return oauth.Response(401, b'{"error":"bad signature"}')
        if path == "/iserver/auth/ssodh/init":
            return oauth.Response(200, b'{"authenticated":true}')
        if path == "/iserver/auth/status":
            return oauth.Response(200, b'{"authenticated":true,"connected":true,"competing":false}')
        if path == "/iserver/accounts":
            return oauth.Response(200, b'{"accounts":["U1"]}')
        if path == md.SEARCH_PATH:
            return oauth.Response(200, json.dumps([
                {"conid": "999", "symbol": "SPX", "description": "CME",
                 "sections": [{"secType": "FUT", "exchange": "CME"}]},
                {"conid": "416904", "symbol": "SPX", "description": "CBOE",
                 "sections": [{"secType": "IND", "exchange": "CBOE"}]},
            ]).encode())
        if path == md.HISTORY_PATH:
            return oauth.Response(200, json.dumps(self.history_answers.pop(0)).encode())
        if path == "/logout":
            return oauth.Response(200, b'{"status":true}')
        return oauth.Response(404, b"")


class OAuthClientTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeIbkr()
        self.client = oauth.IbkrWebClient(self.server.credentials(), transport=self.server,
                                          base_url=oauth.BASE_URL, sleep=lambda _: None)

    def test_live_session_token_matches_the_one_ibkr_derives(self):
        token = self.client.live_session_token()
        self.assertEqual(base64.b64decode(token), self.server.lst)

    def test_token_is_reused_until_near_expiry(self):
        self.client.live_session_token()
        self.client.live_session_token()
        self.assertEqual([c for c in self.server.calls if c[1] == oauth.LIVE_SESSION_TOKEN_PATH],
                         [("POST", oauth.LIVE_SESSION_TOKEN_PATH)])

    def test_signed_calls_pass_ibkr_signature_check_and_session_opens(self):
        status = self.client.open_brokerage_session()
        self.assertTrue(status["authenticated"])
        self.assertTrue(self.client.session_open)
        self.assertEqual(self.client.get_json("/iserver/accounts"), {"accounts": ["U1"]})

    def test_wrong_dh_answer_is_refused(self):
        def tampered(method, url, headers, body):
            answer = self.server(method, url, headers, body)
            if url.endswith(oauth.LIVE_SESSION_TOKEN_PATH):
                payload = json.loads(answer.body)
                payload["live_session_token_signature"] = "00" * 20
                return oauth.Response(200, json.dumps(payload).encode())
            return answer
        client = oauth.IbkrWebClient(self.server.credentials(), transport=tampered)
        with self.assertRaisesRegex(oauth.OAuthError, "signature check"):
            client.live_session_token()

    def test_repr_and_errors_never_show_credentials(self):
        creds = self.server.credentials()
        self.assertNotIn("accesstoken123", repr(creds))
        self.assertNotIn(self.server.access_token_secret, repr(creds))

    def test_settings_come_from_the_environment(self):
        env = {name: "x" for name in oauth.ENV.values()}
        env.update({"IBKR_OAUTH_SIGNATURE_KEY": self.server.signature_pem.decode(),
                    "IBKR_OAUTH_ENCRYPTION_KEY": self.server.encryption_pem.decode(),
                    "IBKR_OAUTH_DH_PRIME": format(PRIME, "x"), "IBKR_OAUTH_CONSUMER_KEY": "testcons"})
        creds = oauth.OAuthCredentials.from_environment(env)
        self.assertEqual((creds.consumer_key, creds.realm, creds.dh_prime), ("TESTCONS", "test_realm", PRIME))
        with self.assertRaisesRegex(oauth.OAuthError, "IBKR_OAUTH_ACCESS_TOKEN"):
            oauth.OAuthCredentials.from_environment({k: v for k, v in env.items() if k != "IBKR_OAUTH_ACCESS_TOKEN"})

    def test_java_byte_array_adds_a_sign_byte_only_when_the_top_bit_is_set(self):
        self.assertEqual(oauth.java_byte_array(0x7F), b"\x7f")
        self.assertEqual(oauth.java_byte_array(0x80), b"\x00\x80")
        self.assertEqual(oauth.java_byte_array(0x1FF), b"\x01\xff")


class MarketDataTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeIbkr()
        self.client = oauth.IbkrWebClient(self.server.credentials(), transport=self.server)
        self.captured = []
        self.reader = md.WebMarketData(self.client, sleep=lambda _: None,
                                       capture=lambda *a, **k: self.captured.append((a, k)) or "blob-1")

    def test_benchmarks_parse_like_the_live_collector(self):
        self.assertEqual(md.parse_benchmark("ind:spx:cboe:usd"), md.Benchmark("IND", "SPX", "CBOE", "USD"))
        self.assertEqual(md.parse_benchmark("IND:SPX:CBOE:USD@416904").conid, "416904")
        with self.assertRaises(ValueError):
            md.parse_benchmark("^GSPC")

    def test_conid_is_the_one_listed_on_the_benchmark_exchange(self):
        self.assertEqual(self.reader.resolve(md.parse_benchmark("IND:SPX:CBOE:USD")), "416904")

    def test_bars_are_dated_on_the_exchange_calendar_and_gaps_dropped(self):
        # 2026-10-21 09:30 New York and 2026-10-22 09:30 Hong Kong (01:30 UTC).
        self.server.history_answers = [{"data": []}, {"data": [
            {"o": 6700.1, "h": 6750, "l": 6690, "c": 6735.25, "v": 0, "t": 1792589400000},
            {"o": 1, "h": 1, "l": 1, "c": None, "v": 0, "t": 1792675800000},
        ]}]
        bars = self.reader.daily_bars(md.parse_benchmark("IND:SPX:CBOE:USD"))
        self.assertEqual([(b.bar_date, b.close) for b in bars], [(date(2026, 10, 21), Decimal("6735.25"))])
        self.assertEqual(bars[0].raw_blob, "blob-1")
        # The empty first answer was retried, and both answers went to bronze before parsing.
        self.assertEqual(len(self.captured), 2)
        args, kwargs = self.captured[0]
        self.assertEqual(args[0], "ibkr-web-history")
        self.assertNotIn("oauth", kwargs["url"].lower())

    def test_hong_kong_bar_keeps_its_local_date(self):
        bars = md.parse_history(md.parse_benchmark("IND:HSI:HKFE:HKD"), "1", {"data": [
            {"c": 25000, "t": 1792603800000}]})  # 2026-10-22 01:30 HKT is still 21 Oct in UTC
        self.assertEqual(bars[0].bar_date, date(2026, 10, 22))

    def test_one_failing_benchmark_does_not_stop_the_rest(self):
        self.server.history_answers = [{"data": [{"c": 10, "t": 1792589400000}]}]
        out = self.reader.fetch([md.parse_benchmark("IND:NOPE:KSE:KRW"), md.parse_benchmark("IND:SPX:CBOE:USD@416904")])
        self.assertIsInstance(out["IND:NOPE:KSE:KRW"], str)
        self.assertEqual(len(out["IND:SPX:CBOE:USD"]), 1)


class OwnerScriptTests(unittest.TestCase):
    """Key generator and Key Vault store script, with Azure faked."""

    def setUp(self):
        import importlib.util
        import sys
        import tempfile
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        self.scripts = {}
        for name in ("ibkr_oauth_keygen", "store_ibkr_oauth"):
            spec = importlib.util.spec_from_file_location(name, root / "scripts" / f"{name}.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            self.scripts[name] = module
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.folder)

    def test_keys_are_made_once_and_stored_without_echoing(self):
        written = self.scripts["ibkr_oauth_keygen"].make(self.folder, dh_bits=512)
        self.assertEqual(set(written), set(self.scripts["ibkr_oauth_keygen"].FILES))
        with self.assertRaises(SystemExit):
            self.scripts["ibkr_oauth_keygen"].make(self.folder, dh_bits=512)
        # A 512-bit test prime keeps the test fast; the store script wants a real-size one.
        (self.folder / "dh_prime.txt").write_text(format(PRIME, "x"), encoding="ascii")

        class Vault:
            BASE, VAULT = "/sub/rg/", "vault1"

            def __init__(self):
                self.secrets = {"ibkr-oauth-access-token": "old"}

            def request(self, method, url, data=None, missing=False):
                name = url.split("/secrets/")[1].split("?")[0]
                if method == "GET":
                    return self.secrets.get(name)
                self.secrets[name] = data["properties"]["value"]
                return {}

        vault = Vault()
        answers = iter(["wealthrys", "wealthrys", "c2VjcmV0", "c2VjcmV0"])
        from contextlib import redirect_stdout
        from io import StringIO

        out = StringIO()
        with redirect_stdout(out):
            self.scripts["store_ibkr_oauth"].main([str(self.folder)], prompt=lambda _: next(answers), stage1=vault)
        self.assertEqual(vault.secrets["ibkr-oauth-consumer-key"], "WEALTHRYS")
        self.assertEqual(vault.secrets["ibkr-oauth-access-token"], "old")  # kept without --replace
        self.assertIn("PRIVATE KEY", vault.secrets["ibkr-oauth-signature-key"])
        self.assertNotIn("c2VjcmV0", out.getvalue())
        self.assertNotIn("PRIVATE", out.getvalue())


if __name__ == "__main__":
    unittest.main()
