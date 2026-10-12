"""Pure, bounded framing for a COMPLETE isolated catalog or synthetic canary.

No IO, environment, SQL, Azure, credentials, logging or execution is provided.
Binding is a reviewed-input contract, NOT source-byte, task, identity, permission,
log-delivery, protected-retention or provider-semantic proof. Trusted controllers
must independently bind actual execution/source and validate the decoded record.
The provider catalog and the no-secret canary are two exact registered purposes.

Encoding validates the whole JSON object before returning any frames, never
samples it, and returns one final manifest. Decoding accepts reordered delivery,
not duplicate delivery, and requires every exact chunk and exactly one manifest.
Frames are ASCII, canonical JSON/Base64 and bounded by UTF-8 byte length. A header
with data_b64 removed is only a structural oracle: it cannot prove payload/hash
integrity. Missing, truncated or unknown log rows must not cause SQL redispatch.
This codec cannot establish that Azure delivered all rows; a separately qualified
reader must check query completeness, scope, multiplicity and actual log behavior.
"""
from dataclasses import dataclass
import base64
import binascii
import hashlib
import json
import math
import re
import uuid

from . import owner_codex_isolated_target as registry


PREFIX = "OWNER_TASK_CATALOG_CHUNK_V1 "
MAX_RECORD_BYTES = 4 * 1024 * 1024
CHUNK_BYTES = 4096
MAX_CHUNKS = 1024
MAX_LINE_BYTES = 8192
MAX_JSON_DEPTH = 64
PROVIDER_JOB_ID = registry.JOB_ID
CANARY_JOB_ID = registry.BASE + "Microsoft.App/jobs/ibkrv2stg-ownertrial-log-canary"
BINDING_FIELDS = ("purpose", "profile_key", "job_resource_id", "operation_id", "source_sha256")
_SHA = re.compile(r"[0-9a-f]{64}")
_CHUNK_FIELDS = frozenset(BINDING_FIELDS + ("kind", "index", "byte_count", "sha256", "data_b64"))
_MANIFEST_FIELDS = frozenset(BINDING_FIELDS + ("kind", "chunk_count", "record_bytes", "record_sha256",
    "chunk_metadata_sha256", "chunk_bytes"))
_CODES = frozenset({"transport_binding_invalid", "transport_record_invalid", "transport_record_oversized",
    "transport_json_invalid", "transport_line_invalid", "transport_line_oversized", "transport_frame_invalid",
    "transport_binding_mismatch", "transport_chunk_invalid", "transport_chunk_hash_mismatch",
    "transport_stream_invalid", "transport_record_hash_mismatch", "transport_metadata_hash_mismatch"})


class TransportError(ValueError):
    """Fixed codes only; never reflects catalog, log, exception or credential text."""
    def __init__(self, code):
        self.code = code if type(code) is str and code in _CODES else "transport_frame_invalid"
        super().__init__(self.code)


def _require(condition, code):
    if not condition:
        raise TransportError(code)


def _sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class Binding:
    purpose: str
    profile_key: str
    job_resource_id: str
    operation_id: str
    source_sha256: str

    def __post_init__(self):
        _require(type(self) is Binding and all(type(getattr(self, key)) is str for key in BINDING_FIELDS),
                 "transport_binding_invalid")
        target = registry.registered_target()
        _require(self.purpose in ("provider", "canary") and self.profile_key == target.profile_key
            and self.job_resource_id == (target.catalog_job_resource_id if self.purpose == "provider" else CANARY_JOB_ID)
            and _sha(self.source_sha256), "transport_binding_invalid")
        try:
            _require(str(uuid.UUID(self.operation_id)) == self.operation_id, "transport_binding_invalid")
        except (ValueError, AttributeError, TypeError):
            raise TransportError("transport_binding_invalid") from None


def _binding(value):
    _require(type(value) is Binding, "transport_binding_invalid")
    try:
        value.__post_init__()  # Frozen Python objects can still be forged by object.__setattr__.
        fields = {key: getattr(value, key) for key in BINDING_FIELDS}
        Binding(**fields)  # Revalidate the exact snapshot without deepcopy/caller hooks.
        return fields  # Copy strings, never retain a mutable caller-provided descriptor.
    except TransportError:
        raise
    except Exception:
        raise TransportError("transport_binding_invalid") from None


def _json_budget(value):
    """Validate only JSON-native exact types and bound serialization before dump.

    The running count is the exact ensure_ascii canonical size. Cycles, excessive
    depth, non-string keys, subclass hooks and non-finite numbers are rejected.
    Surrogate code points are not accepted as Unicode scalar values.
    """
    total = 0
    active = set()

    def add(count):
        nonlocal total
        total += count
        _require(total <= MAX_RECORD_BYTES, "transport_record_oversized")

    def string(text):
        add(2)
        for char in text:
            point = ord(char)
            _require(not 0xD800 <= point <= 0xDFFF, "transport_record_invalid")
            add(2 if char in '\\"\b\f\n\r\t' else 6 if point < 32 or 126 < point <= 0xFFFF
                else 12 if point > 0xFFFF else 1)

    def visit(item, depth):
        _require(depth <= MAX_JSON_DEPTH, "transport_record_invalid")
        kind = type(item)
        if kind is str:
            string(item)
        elif item is None:
            add(4)
        elif kind is bool:
            add(4 if item else 5)
        elif kind is int:
            try:
                add(len(str(item)))
            except (ValueError, OverflowError):
                raise TransportError("transport_record_invalid") from None
        elif kind is float:
            _require(math.isfinite(item), "transport_record_invalid")
            add(len(json.dumps(item, allow_nan=False)))
        elif kind in (dict, list):
            _require(id(item) not in active, "transport_record_invalid")
            add(2 + max(0, len(item) - 1))
            active.add(id(item))
            try:
                if kind is dict:
                    for key, child in item.items():
                        _require(type(key) is str, "transport_record_invalid")
                        string(key)
                        add(1)
                        visit(child, depth + 1)
                else:
                    for child in item:
                        visit(child, depth + 1)
            finally:
                active.remove(id(item))
        else:
            raise TransportError("transport_record_invalid")

    visit(value, 0)
    return total


def canonical(value):
    """Bounded, sorted, ASCII, compact JSON bytes; does not confer authority."""
    try:
        size = _json_budget(value)
        result = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                            allow_nan=False).encode("ascii")
        _require(len(result) == size and len(result) <= MAX_RECORD_BYTES, "transport_record_invalid")
        return result
    except TransportError:
        raise
    except Exception:
        raise TransportError("transport_record_invalid") from None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "transport_json_invalid")
        result[key] = value
    return result


def _constant(_value):
    raise TransportError("transport_json_invalid")


def _load(text):
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except TransportError:
        raise
    except Exception:
        raise TransportError("transport_json_invalid") from None


def _integer(value, lower, upper):
    return type(value) is int and lower <= value <= upper


def parse_header(header, binding):
    """Exact structural header, NOT payload/hash/auth/query-completeness proof.

    The reader must reject duplicate keys when decoding its derived Header JSON.
    Chunk headers omit ONLY data_b64; manifest headers omit nothing. Actual raw
    lines, hashes and the complete decoded record remain independently required.
    """
    expected = _binding(binding)
    _require(type(header) is dict and all(type(key) is str for key in header), "transport_frame_invalid")
    _require(type(header.get("kind")) is str, "transport_frame_invalid")
    kind = header["kind"]
    fields = _CHUNK_FIELDS - {"data_b64"} if kind == "chunk" else _MANIFEST_FIELDS
    _require(kind in ("chunk", "manifest") and set(header) == fields, "transport_frame_invalid")
    _require(all(type(header[k]) is str and header[k] == v for k, v in expected.items()),
             "transport_binding_mismatch")
    if kind == "chunk":
        _require(_integer(header["index"], 0, MAX_CHUNKS - 1)
            and _integer(header["byte_count"], 1, CHUNK_BYTES) and _sha(header["sha256"]),
            "transport_chunk_invalid")
    else:
        _require(_integer(header["chunk_bytes"], CHUNK_BYTES, CHUNK_BYTES)
            and _integer(header["chunk_count"], 1, MAX_CHUNKS)
            and _integer(header["record_bytes"], 2, MAX_RECORD_BYTES)
            and header["chunk_count"] == (header["record_bytes"] + CHUNK_BYTES - 1) // CHUNK_BYTES
            and _sha(header["record_sha256"]) and _sha(header["chunk_metadata_sha256"]),
            "transport_frame_invalid")
    return dict(header)


def _payload(chunk):
    encoded = chunk["data_b64"]
    size = chunk["byte_count"]
    _require(type(encoded) is str and encoded.isascii() and len(encoded) == 4 * ((size + 2) // 3),
             "transport_chunk_invalid")
    try:
        value = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise TransportError("transport_chunk_invalid") from None
    _require(len(value) == size and base64.b64encode(value).decode("ascii") == encoded,
             "transport_chunk_invalid")
    _require(hashlib.sha256(value).hexdigest() == chunk["sha256"], "transport_chunk_hash_mismatch")
    return value


def parse_line(line, binding):
    """Parse one complete ASCII canonical frame; no stripping or line repair."""
    _binding(binding)
    _require(type(line) is str, "transport_line_invalid")
    _require(len(line) <= MAX_LINE_BYTES, "transport_line_oversized")
    _require(line.isascii(), "transport_line_invalid")  # ASCII character length is exact UTF-8 byte length.
    _require(line.startswith(PREFIX), "transport_line_invalid")
    frame = _load(line[len(PREFIX):])
    _require(type(frame) is dict, "transport_frame_invalid")
    header = {k: v for k, v in frame.items() if k != "data_b64"}
    parse_header(header, binding)
    if frame["kind"] == "chunk":
        _require(set(frame) == _CHUNK_FIELDS, "transport_frame_invalid")
        _payload(frame)
    else:
        _require(set(frame) == _MANIFEST_FIELDS, "transport_frame_invalid")
    _require(canonical(frame).decode("ascii") == line[len(PREFIX):], "transport_json_invalid")
    return dict(frame)


def _metadata(chunks):
    return [{k: chunk[k] for k in ("index", "byte_count", "sha256")} for chunk in chunks]


def _line(frame):
    result = PREFIX + canonical(frame).decode("ascii")
    _require(result.isascii() and len(result.encode("utf-8")) <= MAX_LINE_BYTES, "transport_line_oversized")
    return result


def encode_record(record, binding):
    """Validate complete object first; return chunks then exactly one manifest."""
    fields = _binding(binding)
    _require(type(record) is dict, "transport_record_invalid")
    payload = canonical(record)
    chunks = []
    for index, offset in enumerate(range(0, len(payload), CHUNK_BYTES)):
        part = payload[offset:offset + CHUNK_BYTES]
        chunks.append(dict(fields, kind="chunk", index=index, byte_count=len(part),
            sha256=hashlib.sha256(part).hexdigest(), data_b64=base64.b64encode(part).decode("ascii")))
    _require(1 <= len(chunks) <= MAX_CHUNKS, "transport_record_oversized")
    manifest = dict(fields, kind="manifest", chunk_count=len(chunks), record_bytes=len(payload),
        record_sha256=hashlib.sha256(payload).hexdigest(),
        chunk_metadata_sha256=hashlib.sha256(canonical(_metadata(chunks))).hexdigest(), chunk_bytes=CHUNK_BYTES)
    return [_line(chunk) for chunk in chunks] + [_line(manifest)]


def decode_record(lines, binding):
    """Require all exact frames; reordering is not an execution-order assertion."""
    _binding(binding)
    _require(type(lines) is list and 2 <= len(lines) <= MAX_CHUNKS + 1, "transport_stream_invalid")
    chunks = {}
    manifest = None
    for line in lines:
        frame = parse_line(line, binding)
        if frame["kind"] == "manifest":
            _require(manifest is None, "transport_stream_invalid")
            manifest = frame
        else:
            index = frame["index"]
            _require(index not in chunks, "transport_stream_invalid")
            chunks[index] = frame
    _require(manifest is not None and len(chunks) == manifest["chunk_count"]
        and set(chunks) == set(range(manifest["chunk_count"]))
        and len(lines) == manifest["chunk_count"] + 1, "transport_stream_invalid")
    ordered = [chunks[index] for index in range(manifest["chunk_count"])]
    _require(all(chunk["byte_count"] == CHUNK_BYTES for chunk in ordered[:-1])
        and ordered[-1]["byte_count"] == manifest["record_bytes"] - CHUNK_BYTES * (len(ordered) - 1),
        "transport_stream_invalid")
    payload = b"".join(_payload(chunk) for chunk in ordered)
    _require(len(payload) == manifest["record_bytes"]
        and hashlib.sha256(payload).hexdigest() == manifest["record_sha256"], "transport_record_hash_mismatch")
    _require(hashlib.sha256(canonical(_metadata(ordered))).hexdigest() == manifest["chunk_metadata_sha256"],
             "transport_metadata_hash_mismatch")
    try:
        record = _load(payload.decode("ascii"))
    except (ValueError, UnicodeError):
        raise TransportError("transport_json_invalid") from None
    _require(type(record) is dict, "transport_record_invalid")
    _require(canonical(record) == payload, "transport_json_invalid")
    return record
