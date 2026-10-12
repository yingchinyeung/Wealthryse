"""Synthetic framing contracts only; no Azure log delivery or SQL acceptance."""
import ast
import base64
from dataclasses import FrozenInstanceError, asdict
import hashlib
import json
from pathlib import Path
import unittest

from ibkr_etl import owner_codex_catalog_transport as codec
from ibkr_etl import owner_codex_isolated_target as registry


OPERATION = "9f9d44a5-3082-4932-8362-34750b6e3702"


def binding(purpose="canary"):
    return codec.Binding(purpose, registry.PROFILE_KEY,
        codec.CANARY_JOB_ID if purpose == "canary" else registry.JOB_ID, OPERATION, "a" * 64)


def frame(line):
    return json.loads(line[len(codec.PREFIX):])


def line(value):
    return codec.PREFIX + codec.canonical(value).decode("ascii")


def record_with_bytes(size):
    # Exact compact ASCII object overhead is eight bytes: {"p":""}.
    return {"p": "x" * (size - 8)}


def raw_stream(payload, scope=None, widths=None):
    """Forged, internally hash-consistent frames for negative decoder fixtures."""
    scope = scope or binding()
    fields = asdict(scope)
    parts = []
    if widths is None:
        parts = [payload[i:i + codec.CHUNK_BYTES] for i in range(0, len(payload), codec.CHUNK_BYTES)]
    else:
        offset = 0
        for width in widths:
            parts.append(payload[offset:offset + width]); offset += width
        if offset != len(payload): raise AssertionError("Bad synthetic partition")
    chunks = [dict(fields, kind="chunk", index=i, byte_count=len(part),
        sha256=hashlib.sha256(part).hexdigest(), data_b64=base64.b64encode(part).decode("ascii"))
        for i, part in enumerate(parts)]
    metadata = [{k: c[k] for k in ("index", "byte_count", "sha256")} for c in chunks]
    manifest = dict(fields, kind="manifest", chunk_count=len(chunks), record_bytes=len(payload),
        record_sha256=hashlib.sha256(payload).hexdigest(),
        chunk_metadata_sha256=hashlib.sha256(codec.canonical(metadata)).hexdigest(), chunk_bytes=codec.CHUNK_BYTES)
    return [line(c) for c in chunks] + [line(manifest)]


class CatalogTransportTests(unittest.TestCase):
    def assert_rejected(self, call, code=None):
        with self.assertRaises(codec.TransportError) as error:
            call()
        if code is not None: self.assertEqual(code, error.exception.code)
        self.assertEqual(error.exception.code, str(error.exception))
        self.assertNotIn("synthetic-secret", str(error.exception))

    def test_empty_object_unicode_nested_json_and_reordered_roundtrip(self):
        records = ({}, {"unicode": "中😀", "quoted": '\\"\n\t\b\f\r\u0000\u007f',
            "nested": [None, True, False, -4, 0, 1.25, {"a": "x", "z": []}]}, record_with_bytes(9000))
        for purpose in ("canary", "provider"):
            scope = binding(purpose)
            for record in records:
                encoded = codec.encode_record(record, scope)
                self.assertEqual(record, codec.decode_record(encoded, scope))
                self.assertEqual(record, codec.decode_record(encoded[::-1], scope))
                self.assertEqual("manifest", frame(encoded[-1])["kind"])
                self.assertEqual(1, sum(frame(v)["kind"] == "manifest" for v in encoded))
                self.assertTrue(all(v.isascii() and len(v.encode("utf-8")) <= codec.MAX_LINE_BYTES for v in encoded))
                self.assertEqual(codec.canonical(record), json.dumps(record, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=True, allow_nan=False).encode("ascii"))

    def test_exact_chunk_and_full_capacity_boundaries(self):
        scope = binding()
        for size, count in ((codec.CHUNK_BYTES, 1), (codec.CHUNK_BYTES + 1, 2),
                            (codec.MAX_RECORD_BYTES, codec.MAX_CHUNKS)):
            record = record_with_bytes(size)
            encoded = codec.encode_record(record, scope)
            manifest = frame(encoded[-1])
            self.assertEqual(size, manifest["record_bytes"])
            self.assertEqual(count, manifest["chunk_count"])
            self.assertEqual(count + 1, len(encoded))
            self.assertEqual(record, codec.decode_record(encoded, scope))
        self.assert_rejected(lambda: codec.encode_record(record_with_bytes(codec.MAX_RECORD_BYTES + 1), scope),
                             "transport_record_oversized")

    def test_canonical_size_budget_matches_all_escape_classes(self):
        values = ["", "normal", "中", "😀", '\\"\b\f\n\r\t\u0000\u001f\u007f',
                  None, True, False, 10, -100, 1e20, -0.0, [], {}, {"😀": ["中", "x"]}]
        for value in values:
            expected = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                  allow_nan=False).encode("ascii")
            self.assertEqual(expected, codec.canonical(value))

    def test_binding_exact_profile_job_purpose_uuid_sha_types(self):
        good = asdict(binding())
        cases = {"purpose": ("other", True), "profile_key": ("old-profile", 1),
            "job_resource_id": (registry.JOB_ID, codec.CANARY_JOB_ID.upper(), codec.CANARY_JOB_ID + "/extra"),
            "operation_id": (OPERATION.upper(), OPERATION.replace("-", ""), True, "not-uuid"),
            "source_sha256": ("A" * 64, "a" * 63, True)}
        for key, bad_values in cases.items():
            for bad in bad_values:
                changed = dict(good); changed[key] = bad
                self.assert_rejected(lambda: codec.Binding(**changed), "transport_binding_invalid")
        provider = binding("provider")
        self.assertEqual(registry.JOB_ID, provider.job_resource_id)
        with self.assertRaises(FrozenInstanceError): provider.purpose = "canary"

    def test_forged_frozen_binding_revalidated_by_every_public_boundary(self):
        good = binding()
        encoded = codec.encode_record({"p": "x"}, good)
        header = frame(encoded[0]); header.pop("data_b64")
        for field, bad in (("purpose", "provider"), ("profile_key", "foreign"),
                           ("job_resource_id", "unregistered"), ("source_sha256", False),
                           ("operation_id", "invalid")):
            damaged = binding(); object.__setattr__(damaged, field, bad)
            for call in (lambda: codec.encode_record({}, damaged),
                         lambda: codec.parse_line(encoded[0], damaged),
                         lambda: codec.parse_header(header, damaged),
                         lambda: codec.decode_record(encoded, damaged)):
                self.assert_rejected(call, "transport_binding_invalid")
        for bad in (None, asdict(good), "binding"):
            self.assert_rejected(lambda: codec.encode_record({}, bad), "transport_binding_invalid")

    def test_top_level_record_must_be_exact_json_object(self):
        class ObjectSubclass(dict): pass
        for bad in (None, [], "text", 1, True, ObjectSubclass(a=1)):
            self.assert_rejected(lambda: codec.encode_record(bad, binding()), "transport_record_invalid")

    def test_invalid_binding_field_never_runs_deepcopy_or_conversion_hooks(self):
        class Hook:
            def __deepcopy__(self, _memo): raise AssertionError("Deepcopy hook must not run")
            def __str__(self): raise AssertionError("Conversion hook must not run")
        damaged = binding(); object.__setattr__(damaged, "source_sha256", Hook())
        self.assert_rejected(lambda: codec.encode_record({}, damaged), "transport_binding_invalid")

    def test_header_nonstring_keys_rejected_before_lookup_or_comparison_hooks(self):
        class Key:
            def __hash__(self): return hash("kind")
            def __eq__(self, _other): raise AssertionError("Key comparison hook must not run")
        self.assert_rejected(lambda: codec.parse_header({Key(): "chunk"}, binding()), "transport_frame_invalid")

    def test_json_native_types_no_alias_hooks_nan_surrogates_cycles_or_depth(self):
        class Hook:
            def __str__(self): raise AssertionError("Caller hook must not run")
        class ListSubclass(list): pass
        cyclic = []; cyclic.append(cyclic)
        deep = {}
        for _ in range(codec.MAX_JSON_DEPTH + 1): deep = {"a": deep}
        values = ({1: "non-string key"}, {"x": (1, 2)}, {"x": {1, 2}}, {"x": b"bytes"},
            {"x": Hook()}, {"x": ListSubclass()}, {"x": float("nan")}, {"x": float("inf")},
            {"x": float("-inf")}, {"x": "\ud800"}, {"x": cyclic}, deep)
        for value in values:
            self.assert_rejected(lambda: codec.encode_record(value, binding()), "transport_record_invalid")

    def test_line_prefix_type_ascii_utf8_length_and_no_newline_repair(self):
        scope = binding(); good = codec.encode_record({}, scope)[0]
        for bad in (good.encode(), None, "foreign " + good, "中" + good,
                    good + "\n", good + "\r\n", good + " ", " " + good, codec.PREFIX + "\ufeff{}"):
            self.assert_rejected(lambda: codec.parse_line(bad, scope))
        self.assert_rejected(lambda: codec.parse_line(codec.PREFIX + "x" * codec.MAX_LINE_BYTES, scope),
                             "transport_line_oversized")

    def test_chunk_header_is_structural_only_and_omits_exactly_data(self):
        scope = binding(); encoded = codec.encode_record({}, scope)
        full = frame(encoded[0]); header = dict(full); header.pop("data_b64")
        self.assertEqual(header, codec.parse_header(header, scope))
        changed = dict(header, sha256="b" * 64)
        self.assertEqual(changed, codec.parse_header(changed, scope))  # No payload, so cannot prove its SHA.
        returned = codec.parse_header(header, scope); returned["index"] = 42
        self.assertEqual(0, header["index"])
        self.assert_rejected(lambda: codec.parse_header(full, scope), "transport_frame_invalid")
        manifest = frame(encoded[-1])
        self.assertEqual(manifest, codec.parse_header(manifest, scope))

    def test_unknown_missing_extra_or_wrong_kind_fields_rejected(self):
        scope = binding(); encoded = codec.encode_record({}, scope)
        for position in (0, -1):
            good = frame(encoded[position])
            for field in good:
                value = dict(good); value.pop(field)
                self.assert_rejected(lambda: codec.parse_line(line(value), scope))
            for bad in (dict(good, unknown=True), dict(good, kind="future"), dict(good, kind=True)):
                self.assert_rejected(lambda: codec.parse_line(line(bad), scope))
        manifest = dict(frame(encoded[-1]), data_b64="e30=")
        self.assert_rejected(lambda: codec.parse_line(line(manifest), scope), "transport_frame_invalid")

    def test_foreign_binding_on_chunk_or_manifest_rejected(self):
        scope = binding(); encoded = codec.encode_record({}, scope)
        changes = (("purpose", "provider"), ("profile_key", "foreign"), ("job_resource_id", registry.JOB_ID),
            ("operation_id", "1875bdf9-15ab-4ddc-9d1e-502b65bd02f2"), ("source_sha256", "b" * 64))
        for position in (0, -1):
            for key, bad in changes:
                changed = frame(encoded[position]); changed[key] = bad
                self.assert_rejected(lambda: codec.parse_line(line(changed), scope), "transport_binding_mismatch")

    def test_strict_chunk_numeric_bounds_and_boolean_aliases(self):
        scope = binding(); good = frame(codec.encode_record({}, scope)[0])
        for key, bad_values in {"index": (False, 0.0, "0", -1, codec.MAX_CHUNKS),
            "byte_count": (True, 2.0, "2", 0, codec.CHUNK_BYTES + 1),
            "sha256": (True, "A" * 64, "a" * 63), "data_b64": (None, True, b"e30=")}.items():
            for bad in bad_values:
                value = dict(good); value[key] = bad
                if type(bad) is bytes:
                    # Already decoded native-object header mutation must not be coerced.
                    self.assert_rejected(lambda: codec.canonical(value), "transport_record_invalid")
                else:
                    self.assert_rejected(lambda: codec.parse_line(line(value), scope))

    def test_strict_manifest_bounds_and_boolean_aliases(self):
        scope = binding(); good = frame(codec.encode_record({}, scope)[-1])
        for key, bad_values in {"chunk_count": (True, 1.0, "1", 0, codec.MAX_CHUNKS + 1, 2),
            "record_bytes": (True, 2.0, "2", 1, codec.MAX_RECORD_BYTES + 1),
            "chunk_bytes": (True, codec.CHUNK_BYTES - 1, codec.CHUNK_BYTES + 1),
            "record_sha256": (False, "A" * 64), "chunk_metadata_sha256": (None, "a" * 63)}.items():
            for bad in bad_values:
                value = dict(good); value[key] = bad
                self.assert_rejected(lambda: codec.parse_line(line(value), scope))

    def test_noncanonical_json_order_spacing_escapes_duplicates_nan_or_bad_json(self):
        scope = binding(); encoded = codec.encode_record({}, scope)
        good = frame(encoded[0]); text = codec.canonical(good).decode("ascii")
        bad_values = (codec.PREFIX + json.dumps(good), codec.PREFIX + json.dumps(good, sort_keys=True),
            codec.PREFIX + "{" + text[1:-1] + ',"index":0}', codec.PREFIX + "{" + text[1:-1] + ',"index":1}',
            codec.PREFIX + text.replace('"index":0', '"index":NaN'),
            codec.PREFIX + text.replace('"index":0', '"index":Infinity'),
            codec.PREFIX + text.replace('"kind":"chunk"', '"kind":"ch\\u0075nk"'),
            codec.PREFIX + "[]", codec.PREFIX + "null", codec.PREFIX + "{", codec.PREFIX + text + "{}")
        for bad in bad_values:
            self.assert_rejected(lambda: codec.parse_line(bad, scope))

    def test_strict_base64_padding_padbits_whitespace_truncation_and_altered_hash(self):
        scope = binding(); good = frame(codec.encode_record({}, scope)[0])
        self.assertEqual("e30=", good["data_b64"])
        for bad in ("e30", "e30==", "e31=", "e30\n", "é30=", "****", "e30 "):
            changed = dict(good, data_b64=bad)
            self.assert_rejected(lambda: codec.parse_line(line(changed), scope))
        self.assert_rejected(lambda: codec.parse_line(line(dict(good, sha256="b" * 64)), scope),
                             "transport_chunk_hash_mismatch")
        self.assert_rejected(lambda: codec.parse_line(line(dict(good, byte_count=3)), scope),
                             "transport_chunk_invalid")

    def test_missing_duplicate_extra_split_or_truncated_rows_fail_closed(self):
        scope = binding(); good = codec.encode_record(record_with_bytes(9000), scope)
        cases = (good[:-1], good[1:], good + [good[0]], good + [good[-1]], [good[-1]], [],
            good + [codec.PREFIX + "{}"], [good[0][:100], good[0][100:]] + good[1:],
            [good[0][:-1]] + good[1:], [good[0]] + good[2:])
        for bad in cases: self.assert_rejected(lambda: codec.decode_record(bad, scope))
        for bad in (tuple(good), iter(good), "\n".join(good), [good[0]] * (codec.MAX_CHUNKS + 2)):
            self.assert_rejected(lambda: codec.decode_record(bad, scope), "transport_stream_invalid")

    def test_missing_index_replaced_by_extra_index_rejected(self):
        scope = binding(); good = codec.encode_record(record_with_bytes(9000), scope)
        changed = frame(good[1]); changed["index"] = 3
        bad = [good[0], line(changed)] + good[2:]
        self.assert_rejected(lambda: codec.decode_record(bad, scope), "transport_stream_invalid")

    def test_nonfull_middle_chunks_even_hash_consistent_rejected(self):
        payload = codec.canonical(record_with_bytes(codec.CHUNK_BYTES + 2))
        forged = raw_stream(payload, widths=(codec.CHUNK_BYTES - 1, 3))
        self.assert_rejected(lambda: codec.decode_record(forged, binding()), "transport_stream_invalid")

    def test_full_record_and_ordered_chunk_metadata_sha_required(self):
        scope = binding(); good = codec.encode_record(record_with_bytes(9000), scope)
        manifest = frame(good[-1])
        for field, code in (("record_sha256", "transport_record_hash_mismatch"),
                            ("chunk_metadata_sha256", "transport_metadata_hash_mismatch")):
            changed = dict(manifest); changed[field] = "b" * 64
            self.assert_rejected(lambda: codec.decode_record(good[:-1] + [line(changed)], scope), code)
        ordered = [frame(v) for v in good[:-1]]
        metadata = [{k: c[k] for k in ("index", "byte_count", "sha256")} for c in ordered]
        self.assertEqual(hashlib.sha256(codec.canonical(metadata)).hexdigest(), manifest["chunk_metadata_sha256"])
        swapped = dict(manifest, chunk_metadata_sha256=hashlib.sha256(codec.canonical(metadata[::-1])).hexdigest())
        self.assert_rejected(lambda: codec.decode_record(good[:-1] + [line(swapped)], scope),
                             "transport_metadata_hash_mismatch")

    def test_hash_consistent_noncanonical_or_nonobject_record_rejected(self):
        for payload in (b'{ "a":1}', b'{"z":1,"a":2}', b'{"a":1,"a":1}', b'{"a":NaN}',
                        b'{"a":Infinity}', b'{"a":1e999}', b'{"a":"\\ud800"}', b'{}\n', b'{}{}',
                        b'[]', b'123', b'{"p":"\xff"}'):
            self.assert_rejected(lambda: codec.decode_record(raw_stream(payload), binding()))

    def test_errors_fixed_never_reflect_unknown_reason_or_payload(self):
        self.assertEqual("transport_frame_invalid", str(codec.TransportError("synthetic-secret")))
        self.assert_rejected(lambda: codec.parse_line(codec.PREFIX + "synthetic-secret", binding()),
                             "transport_json_invalid")

    def test_source_inert_stdlib_and_registry_only_python312_contract(self):
        source = Path(codec.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source, feature_version=(3, 12))
        self.assertFalse(any(isinstance(n, ast.If) for n in tree.body))
        self.assertNotRegex(source, r"os\.environ|psycopg|AzureCliCredential|requests\.|subprocess\.|print\(|open\(")
        imports = [n.module for n in tree.body if isinstance(n, ast.ImportFrom)]
        self.assertEqual(["dataclasses", None], imports)
        self.assertEqual(codec.MAX_RECORD_BYTES, codec.CHUNK_BYTES * codec.MAX_CHUNKS)


if __name__ == "__main__":
    unittest.main()
