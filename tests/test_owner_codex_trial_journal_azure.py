"""Synthetic SDK/ARM boundary tests, not Azure acceptance or caller authentication."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import RequestsTransport
from azure.storage.blob import ContainerClient as RealContainerClient
import requests

from ibkr_etl.owner_codex_trial_lifecycle import LifecycleError
from ibkr_etl.owner_codex_trial_journal import MAX_RECORD_BYTES, key_for_slot
from ibkr_etl.owner_codex_trial_journal_azure import (
    ACCOUNT, CONTAINER, PRIMARY_URL, PROBE_PREFIX, SCOPE, SyntheticAzureJournalBackend)


VERSION = "2026-10-04T09:50:00.1234567Z"
ETAG = '"0x8DE123456789ABC"'


def provider_error(cls, status, code):
    error = cls("sensitive-provider-error-must-not-escape")
    error.status_code, error.error_code = status, code
    return error


def protection():
    return {"id": SCOPE, "properties": {"publicAccess": "None", "hasLegalHold": True,
        "legalHold": {"protectedAppendWritesHistory": {"allowProtectedAppendWritesAll": False}}}}


class Credential:
    def get_token(self, *args):
        raise AssertionError("Synthetic client must not request real credentials")


class FakeContainer:
    account_name, container_name, url = ACCOUNT, CONTAINER, PRIMARY_URL + "/" + CONTAINER

    def __init__(self):
        self.items, self.calls, self.fault = {}, [], None

    def get_blob_client(self, name, *, version_id=None):
        self.calls.append(("client", name, version_id))
        owner = self

        class Blob:
            def get_blob_properties(self, **kwargs):
                owner.calls.append(("properties", name, version_id, kwargs))
                if name not in owner.items:
                    code = "ContainerNotFound" if owner.fault == "container_missing" else "BlobNotFound"
                    raise provider_error(ResourceNotFoundError, 404, code)
                data = owner.items[name]
                result = dict(size=len(data), blob_type="BlockBlob", version_id=VERSION, etag=ETAG)
                if owner.fault == "size_bool": result["size"] = True
                if owner.fault == "size_large": result["size"] = MAX_RECORD_BYTES + 1
                if owner.fault == "type_append": result["blob_type"] = "AppendBlob"
                if owner.fault == "version_missing": result["version_id"] = None
                if owner.fault == "version_drift" and version_id: result["version_id"] = "2026-10-04T10:00:00.1234567Z"
                if owner.fault == "etag_drift" and version_id: result["etag"] = '"0x123"'
                return SimpleNamespace(**result)

            def download_blob(self, **kwargs):
                owner.calls.append(("download", name, version_id, kwargs))
                data = owner.items[name]
                if owner.fault == "truncated": data = data[:-1]
                if owner.fault == "content_wrong": data = b"x" * len(data)
                return SimpleNamespace(readall=lambda: data)

            def upload_blob(self, data, **kwargs):
                owner.calls.append(("upload", name, data, kwargs))
                if owner.fault == "immutable_error":
                    raise provider_error(ResourceExistsError, 409, "BlobImmutableDueToLegalHold")
                if name in owner.items:
                    status, code = (412, "ConditionNotMet") if owner.fault == "condition" else (409, "BlobAlreadyExists")
                    raise provider_error(ResourceExistsError, status, code)
                owner.items[name] = data
                if owner.fault == "lost_response":
                    raise RuntimeError("sensitive timeout after committed upload")
                return {"version_id": None if owner.fault == "ack_missing" else VERSION, "etag": ETAG}

        return Blob()


class AzureJournalTests(unittest.TestCase):
    def setUp(self):
        self.container, self.arm_calls, self.arm_value = FakeContainer(), [], protection()
        self.patch = patch("ibkr_etl.owner_codex_trial_journal_azure.ContainerClient", return_value=self.container)
        self.factory = self.patch.start()
        self.addCleanup(self.patch.stop)

        def arm():
            self.arm_calls.append("read")
            return copy.deepcopy(self.arm_value)

        self.backend = SyntheticAzureJournalBackend(Credential(), arm)
        self.key = key_for_slot(1)

    def assert_uncertain(self, operation):
        with self.assertRaisesRegex(LifecycleError, "^store_uncertain$"):
            operation()

    def test_constructor_inert_explicit_primary_no_retry_and_not_live(self):
        self.assertEqual([], self.arm_calls)
        self.assertEqual([], self.container.calls)
        self.assertIs(False, self.backend.supports_live_execution)
        args, kwargs = self.factory.call_args
        self.assertEqual((PRIMARY_URL, CONTAINER), args)
        self.assertEqual(0, kwargs["retry_total"])
        self.assertEqual(MAX_RECORD_BYTES, kwargs["max_single_put_size"])
        self.assertEqual(5, kwargs["connection_timeout"])
        self.assertEqual(10, kwargs["read_timeout"])
        self.assertIs(False, kwargs["permit_redirects"])
        self.assertEqual(0, kwargs["redirect_max"])

    def test_exact_absence_requires_two_protection_reads(self):
        self.assertIsNone(self.backend.read(self.key))
        self.assertEqual(["read", "read"], self.arm_calls)

    def test_only_exact_blob_not_found_is_absence(self):
        self.container.fault = "container_missing"
        self.assert_uncertain(lambda: self.backend.read(self.key))

    def test_exact_conditional_block_create_and_pinned_readback(self):
        self.assertIs(True, self.backend.create_if_absent(self.key, b"metadata"))
        uploads = [c for c in self.container.calls if c[0] == "upload"]
        self.assertEqual(1, len(uploads))
        self.assertEqual(PROBE_PREFIX + self.key, uploads[0][1])
        options = uploads[0][3]
        self.assertEqual("BlockBlob", options["blob_type"])
        self.assertIs(False, options["overwrite"])
        self.assertNotIn("etag", options)
        self.assertIs(MatchConditions.IfMissing, options["match_condition"])
        self.assertEqual(0, options["retry_total"])
        self.assertNotIn("allow_redirects", options)
        reads = [c for c in self.container.calls if c[0] == "download"]
        self.assertTrue(reads)
        self.assertEqual(VERSION, reads[0][2])
        self.assertEqual(ETAG, reads[0][3]["etag"])
        self.assertIs(MatchConditions.IfNotModified, reads[0][3]["match_condition"])
        self.assertEqual(9, reads[0][3]["length"])

    def test_existing_identical_or_different_slot_never_succeeds(self):
        self.assertIs(True, self.backend.create_if_absent(self.key, b"metadata"))
        for fault, data in ((None, b"metadata"), ("condition", b"different")):
            self.container.fault = fault
            self.assertIs(False, self.backend.create_if_absent(self.key, data))
        self.assertEqual(b"metadata", self.backend.read(self.key))

    def test_lost_ack_is_sanitized_and_not_retried(self):
        self.container.fault = "lost_response"
        self.assert_uncertain(lambda: self.backend.create_if_absent(self.key, b"metadata"))
        self.assertEqual(1, len([c for c in self.container.calls if c[0] == "upload"]))
        self.container.fault = None
        self.assertEqual(b"metadata", self.backend.read(self.key))
        self.assertIs(False, self.backend.create_if_absent(self.key, b"metadata"))

    def test_legal_hold_errors_are_not_lost_cas(self):
        self.container.fault = "immutable_error"
        self.assert_uncertain(lambda: self.backend.create_if_absent(self.key, b"metadata"))

    def test_invalid_key_has_no_provider_calls(self):
        for key in ("../raw-statements", "https://elsewhere", self.key + ".json", True, key_for_slot(10) + "/extra"):
            self.assert_uncertain(lambda key=key: self.backend.read(key))
            self.assert_uncertain(lambda key=key: self.backend.create_if_absent(key, b"metadata"))
        self.assertEqual([], self.container.calls)
        self.assertEqual([], self.arm_calls)

    def test_wrong_account_or_endpoint_is_rejected(self):
        for field, value in (("account_name", "other"), ("container_name", "ibkr-raw"),
                             ("url", PRIMARY_URL + "/" + CONTAINER + "?sig=secret")):
            old = getattr(self.container, field)
            setattr(self.container, field, value)
            self.assert_uncertain(lambda: self.backend.create_if_absent(self.key, b"metadata"))
            setattr(self.container, field, old)
        self.assertFalse(self.container.items)

    def test_protection_shape_and_bool_alias_denials(self):
        original = protection()
        variants = []
        for field, value in (("publicAccess", "Blob"), ("hasLegalHold", 1), ("hasLegalHold", False), ("deleted", 0), ("deleted", True)):
            v = copy.deepcopy(original); v["properties"][field] = value; variants.append(v)
        v = copy.deepcopy(original); v["id"] = SCOPE + "-wrong"; variants.append(v)
        v = copy.deepcopy(original); v["properties"]["legalHold"]["protectedAppendWritesHistory"]["allowProtectedAppendWritesAll"] = True; variants.append(v)
        for v in variants:
            self.arm_value = v
            self.assert_uncertain(lambda: self.backend.create_if_absent(self.key, b"metadata"))
        self.assertFalse(self.container.items)

    def test_bad_properties_reject_read(self):
        self.assertIs(True, self.backend.create_if_absent(self.key, b"metadata"))
        for fault in ("size_bool", "size_large", "type_append", "version_missing", "version_drift", "etag_drift", "truncated"):
            self.container.fault = fault
            self.assert_uncertain(lambda: self.backend.read(self.key))

    def test_write_content_mismatch_or_malformed_ack_burns_ack(self):
        for i, fault in enumerate(("content_wrong", "ack_missing"), 1):
            self.container.fault = fault
            self.assert_uncertain(lambda i=i: self.backend.create_if_absent(key_for_slot(i), b"metadata"))
        self.assertEqual(2, len(self.container.items))
        self.assertEqual(2, len([c for c in self.container.calls if c[0] == "upload"]))

    def test_bad_payload_has_no_provider_calls(self):
        for data in (b"", "metadata", True, b"x" * (MAX_RECORD_BYTES + 1)):
            self.assert_uncertain(lambda data=data: self.backend.create_if_absent(self.key, data))
        self.assertEqual([], self.container.calls)

    def test_protection_loss_after_put_is_uncertain_and_committed(self):
        calls = []

        def arm():
            calls.append(1)
            value = protection()
            if len(calls) > 1: value["properties"]["hasLegalHold"] = False
            return value

        self.backend._arm = arm
        self.assert_uncertain(lambda: self.backend.create_if_absent(self.key, b"metadata"))
        self.assertEqual(b"metadata", self.container.items[PROBE_PREFIX + self.key])


class RealSdkPipelineTests(unittest.TestCase):
    """Real SDK + RequestsTransport with offline Session.request responses.

    This catches unsupported method kwargs/header normalization without network,
    Azure authentication or a claim that the service performed these operations.
    """
    def setUp(self):
        self.calls, self.content, self.put_status = [], None, None
        self.session = requests.Session()
        self.addCleanup(self.session.close)
        self.session.request = self.respond
        self.transport = RequestsTransport(session=self.session, session_owner=False)
        self.addCleanup(self.transport.close)

        class Token:
            def get_token(self, *args, **kwargs):
                return AccessToken("offline-unprinted-placeholder", 4000000000)

        def factory(*args, **kwargs):
            return RealContainerClient(*args, transport=self.transport, **kwargs)

        self.patch = patch("ibkr_etl.owner_codex_trial_journal_azure.ContainerClient", side_effect=factory)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.backend = SyntheticAzureJournalBackend(Token(), protection)
        self.key = key_for_slot(1)

    def respond(self, method, url, *, headers, data=None, files=None, verify=None,
                timeout=None, cert=None, allow_redirects=None, stream=None, proxies=None):
        # Deliberately no **kwargs: unexpected SDK kwargs cannot hide in a fake.
        self.calls.append((method, url, dict(headers), allow_redirects))
        response = requests.Response()
        response.raw = SimpleNamespace(enforce_content_length=False)
        response.headers.update({"x-ms-version-id": VERSION, "ETag": ETAG,
            "Last-Modified": "Sun, 04 Oct 2026 09:50:00 GMT", "x-ms-blob-type": "BlockBlob"})
        response.status_code, response.reason = 200, "OK"
        response._content, response._content_consumed = b"", True
        if method == "PUT":
            status = self.put_status or (412 if self.content is not None else 201)
            response.status_code = status
            if status == 201:
                self.content = data
            elif status == 302:
                response.headers["Location"] = "https://unregistered.example.invalid/"
            else:
                code = "ConditionNotMet" if status == 412 else "AuthorizationFailure"
                response.headers.update({"Content-Type": "application/xml", "x-ms-error-code": code})
                response._content = ("<Error><Code>" + code + "</Code><Message>offline</Message></Error>").encode()
        elif self.content is None:
            response.status_code, response.reason = 404, "Not Found"
            response.headers.update({"x-ms-error-code": "BlobNotFound", "Content-Type": "application/xml"})
            response._content = b""  # A genuine HEAD error has no XML response body.
        else:
            response.headers["Content-Length"] = str(len(self.content))
            if method == "GET":
                response.status_code = 206
                response.headers["Content-Range"] = "bytes 0-" + str(len(self.content) - 1) + "/" + str(len(self.content))
                response._content = self.content
        return response

    def test_real_sdk_emits_if_none_match_and_exact_pinned_reads(self):
        self.assertIs(True, self.backend.create_if_absent(self.key, b"metadata"))
        self.assertEqual(b"metadata", self.backend.read(self.key))
        put = next(c for c in self.calls if c[0] == "PUT")
        self.assertEqual("*", put[2]["If-None-Match"])
        self.assertEqual("BlockBlob", put[2]["x-ms-blob-type"])
        self.assertTrue(all(c[3] is False for c in self.calls))
        gets = [c for c in self.calls if c[0] == "GET"]
        self.assertTrue(gets)
        self.assertTrue(all("versionid=" in c[1] and c[2]["If-Match"] == ETAG for c in gets))

    def test_real_sdk_normalized_412_is_lost_cas(self):
        self.assertIs(True, self.backend.create_if_absent(self.key, b"metadata"))
        self.assertIs(False, self.backend.create_if_absent(self.key, b"metadata"))
        self.assertEqual(2, len([c for c in self.calls if c[0] == "PUT"]))

    def test_real_sdk_redirect_is_not_followed(self):
        self.put_status = 302
        with self.assertRaisesRegex(LifecycleError, "^store_uncertain$"):
            self.backend.create_if_absent(self.key, b"metadata")
        self.assertEqual(1, len(self.calls))

    def test_real_sdk_503_is_not_retried(self):
        self.put_status = 503
        with self.assertRaisesRegex(LifecycleError, "^store_uncertain$"):
            self.backend.create_if_absent(self.key, b"metadata")
        self.assertEqual(1, len(self.calls))

    def test_real_sdk_404_absence_is_bounded(self):
        self.assertIsNone(self.backend.read(self.key))
        self.assertEqual(1, len(self.calls))


if __name__ == "__main__":
    unittest.main()
