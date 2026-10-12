"""Fixed protected Azure backend for a SYNTHETIC journal rehearsal only.

Construction performs no I/O or credential discovery. Trusted manual composition
supplies a credential and an independently obtained ARM container reader. This
class never qualifies LIVE_DISPOSABLE execution, authenticates a model/caller,
opens PostgreSQL, or changes resource configuration. Its probe namespace is
separate from the future real control journal and cannot authorize SQL effects.

Only small block blobs are conditionally created, once. An existing slot is a
lost CAS, including when its bytes match. Unknown responses are never retried.
ACKs are checked against exact version, ETag, bytes and private legal hold.
Primary endpoint only; no SAS, secondary, overwrite, delete or append route.
"""
from __future__ import annotations

from datetime import datetime
import re

from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import ContainerClient

from .owner_codex_trial_lifecycle import LifecycleError
from .owner_codex_trial_journal import MAX_RECORD_BYTES, MAX_SLOTS, key_for_slot
from .specialist_archive_reader import ACCOUNT, CONTAINER, SCOPE


PROBE_PREFIX = "probes/owner-codex-trial-journal-20261004/"
PRIMARY_URL = "https://" + ACCOUNT + ".blob.core.windows.net"
_VERSION = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z")
_ETAG = re.compile(r'"0x[0-9A-Fa-f]+"')
_NAMES = {key_for_slot(n): PROBE_PREFIX + key_for_slot(n)
          for n in range(1, MAX_SLOTS + 1)}


def _require(condition):
    if not condition:
        raise LifecycleError("store_uncertain")


def _version(value):
    _require(type(value) is str and _VERSION.fullmatch(value) is not None)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LifecycleError("store_uncertain") from None


def _etag(value):
    _require(type(value) is str and _ETAG.fullmatch(value) is not None)


class SyntheticAzureJournalBackend:
    """A concrete storage candidate, not a production authority adapter.

    Protection checks cannot prevent an administrator removing protection, and
    this backend is not an identity/approval verifier. No supplied boolean can
    enable live SQL. A successful synthetic probe proves only its observed
    storage operations under the executing credential, not agent containment.
    """
    supports_live_execution = False

    def __init__(self, credential, read_arm_properties):
        _require(callable(read_arm_properties) and callable(getattr(credential, "get_token", None)))
        self._arm = read_arm_properties
        self._container = ContainerClient(PRIMARY_URL, CONTAINER, credential=credential,
            connection_timeout=5, read_timeout=10, retry_total=0,
            retry_to_secondary=False, permit_redirects=False, redirect_max=0,
            max_single_put_size=MAX_RECORD_BYTES)

    def _protection(self):
        response = self._arm()
        _require(type(response) is dict and type(response.get("id")) is str
                 and response["id"].casefold() == SCOPE.casefold())
        p = response.get("properties")
        _require(type(p) is dict and p.get("publicAccess") == "None"
                 and p.get("hasLegalHold") is True and p.get("deleted", False) is False)
        hold = p.get("legalHold", {})
        _require(type(hold) is dict)
        history = hold.get("protectedAppendWritesHistory", {})
        _require(type(history) is dict
                 and history.get("allowProtectedAppendWritesAll", False) is False
                 and history.get("allowProtectedAppendWrites", False) is False)

    def _blob(self, key, *, version=None):
        _require(type(key) is str and key in _NAMES)
        # Revalidate exact fixed endpoint on each operation. These are properties
        # of the client composed above, not authentication claims from an agent.
        _require(self._container.account_name == ACCOUNT
                 and self._container.container_name == CONTAINER
                 and self._container.url.rstrip("/") == PRIMARY_URL + "/" + CONTAINER)
        return self._container.get_blob_client(_NAMES[key], version_id=version)

    def _read_version(self, key, properties, *, expected_etag=None):
        _require(type(properties.size) is int and 0 < properties.size <= MAX_RECORD_BYTES
                 and properties.blob_type == "BlockBlob")
        _version(properties.version_id)
        _etag(properties.etag)
        if expected_etag is not None:
            _require(properties.etag == expected_etag)
        pinned = self._blob(key, version=properties.version_id)
        exact = pinned.get_blob_properties(timeout=10, retry_total=0)
        _require(exact.version_id == properties.version_id and exact.size == properties.size
                 and exact.etag == properties.etag and exact.blob_type == "BlockBlob")
        data = pinned.download_blob(offset=0, length=properties.size + 1,
            etag=properties.etag, match_condition=MatchConditions.IfNotModified,
            max_concurrency=1, timeout=10, retry_total=0).readall()
        _require(type(data) is bytes and len(data) == properties.size)
        return data

    def read(self, key):
        try:
            blob = self._blob(key)
            self._protection()
            try:
                properties = blob.get_blob_properties(timeout=10, retry_total=0)
            except ResourceNotFoundError as error:
                _require(error.status_code == 404 and error.error_code == "BlobNotFound")
                self._protection()
                return None
            data = self._read_version(key, properties)
            self._protection()
            return data
        except Exception:
            # Never return raw provider messages, URLs, request bodies or tokens.
            raise LifecycleError("store_uncertain") from None

    def create_if_absent(self, key, value):
        try:
            _require(type(value) is bytes and 0 < len(value) <= MAX_RECORD_BYTES)
            blob = self._blob(key)
            self._protection()
            try:
                result = blob.upload_blob(value, length=len(value), blob_type="BlockBlob",
                    overwrite=False, match_condition=MatchConditions.IfMissing,
                    max_concurrency=1, timeout=10, retry_total=0)
            except ResourceExistsError as error:
                _require((error.status_code, error.error_code) in
                         {(409, "BlobAlreadyExists"), (412, "ConditionNotMet"),
                          (412, "BlobAlreadyExists")})
                self._protection()
                return False  # Matching bytes do not recover the lost permit.
            _require(type(result) is dict)
            _version(result.get("version_id"))
            _etag(result.get("etag"))
            properties = self._blob(key, version=result["version_id"]).get_blob_properties(
                timeout=10, retry_total=0)
            _require(properties.version_id == result["version_id"])
            _require(self._read_version(key, properties, expected_etag=result["etag"]) == value)
            self._protection()
            return True
        except Exception:
            # A PUT may have committed: no replay, repair or overwrite here.
            raise LifecycleError("store_uncertain") from None
