"""Cheap synthetic execution-family oracles; never query Azure or prove absence."""
from copy import deepcopy
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, asdict
import hashlib
import io
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import read_owner_task_catalog_family as family
import read_owner_task_catalog_logs as reader
import test_owner_task_catalog_logs as fixtures
import test_owner_task_catalog_canary_readback as readback_fixtures
from ibkr_etl import owner_codex_catalog_transport as codec


def scope(purpose="provider"):
    job = "ibkrv2stg-ownertrial-catalog" if purpose == "provider" else "ibkrv2stg-ownertrial-log-canary"
    captured = reader.CatalogLogScope(fixtures.binding(purpose), job + "-9hh541u-replica-b7", "catalog",
        "2026-10-04T14:00:00Z", "2026-10-04T15:00:00Z")
    return family.CatalogFamilyScope(captured, job + "-9hh541u")


def canonical(value):
    # Independent oracle, deliberately does not call the implementation's hash.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode("ascii")


def parsed_rows(rows):
    result = [dict(zip((name for name, _kind in fixtures.HEADER_COLUMNS), row)) for row in rows]
    for row in result:
        row["Header"] = json.loads(row["Header"])
    return result


class FamilyFixture:
    def __init__(self, purpose="provider"):
        self.scope = scope(purpose)
        self.captured = fixtures.QueryFixture({"synthetic": True, "sql_acceptance": False,
            "data": "no-credentials"}, self.scope.replica_scope)
        self.before = deepcopy(self.captured.inventory)
        self.after = deepcopy(self.before)
        self.family_requests = []
        self.events = []
        self.response_mutation = None

    def family_query(self, query):
        self.family_requests.append(query)
        self.events.append("family")
        rows = self.before if len(self.family_requests) == 1 else self.after
        value = fixtures.table(deepcopy(rows))
        return self.response_mutation(value) if self.response_mutation else value

    def captured_query(self, query):
        self.events.append(query.kind)
        return self.captured(query)

    def collect(self):
        return family.collect_evidence(self.family_query, self.captured_query, self.scope)


class FamilyErrors(unittest.TestCase):
    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(reader.CatalogLogsError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def denies_before_captured(self, fixture, code="logs_inventory_invalid"):
        self.error(code, fixture.collect)
        self.assertEqual(fixture.captured.requests, [])
        self.assertEqual(len(fixture.family_requests), 1)


class ScopeAndQueryTests(FamilyErrors):
    def test_provider_and_canary_exact_broad_query_oracle(self):
        for purpose in ("provider", "canary"):
            target = scope(purpose)
            query = family.CatalogFamilyQuery(target)
            expected = reader.CatalogQuery(target.replica_scope, "inventory").text.replace(
                "ContainerGroupName_s == '" + target.replica_scope.log_group + "'",
                "ContainerGroupName_s startswith '" + target.execution_name + "-'")
            self.assertEqual(query.text, expected)
            self.assertIn("Log_s startswith 'OWNER_TASK_CATALOG_CHUNK'", query.text)
            self.assertIn("TimeGenerated >= datetime(2026-10-04T14:00:00Z)", query.text)
            self.assertIn("TimeGenerated < datetime(2026-10-04T15:00:00Z)", query.text)
            self.assertNotIn("OWNER_TASK_CATALOG_CHUNK_V1", query.text)
            for forbidden in ("| take ", "| top ", "| distinct ", "| summarize ", "| sample ",
                "ago(", "ContainerName_s ==", "Envelope.purpose", "Envelope.operation_id",
                "Envelope.source_sha256", "Envelope.kind", target.replica_scope.binding.operation_id):
                self.assertNotIn(forbidden, query.text)

    def test_scope_and_query_are_frozen(self):
        target = scope()
        query = family.CatalogFamilyQuery(target)
        with self.assertRaises(FrozenInstanceError):
            target.execution_name = "foreign"
        with self.assertRaises(FrozenInstanceError):
            query.scope = target

    def test_execution_must_match_registered_purpose_and_exact_bounded_suffix(self):
        target = scope()
        job = "ibkrv2stg-ownertrial-catalog"
        for name in (None, True, "", job, job + "-", job + "-" + "x" * 6, job + "-" + "x" * 13,
            job + "-x'", job + "-x y", job + "-x/", job + "-x\n",
            job + "-ABC1234", job + "-a_b1234", job + "-a-b1234",
            "ibkrv2stg-ownertrial-log-canary-9hh541u", job.upper() + "-9hh541u"):
            with self.subTest(name=repr(name)):
                self.error("logs_scope_invalid", family.CatalogFamilyScope, target.replica_scope, name)
        for suffix in ("9hh541u", "a" * 7, "a" * 12):
            replica = reader.CatalogLogScope(fixtures.binding(), job + "-" + suffix + "-r", "catalog",
                target.replica_scope.start_utc, target.replica_scope.end_utc)
            self.assertEqual(family.CatalogFamilyScope(replica, job + "-" + suffix).execution_name,
                job + "-" + suffix)

    def test_group_requires_execution_hyphen_and_nonempty_replica_suffix(self):
        target = scope()
        for group in (target.execution_name, target.execution_name + "-", target.execution_name + "_r",
            target.execution_name + "extra-r", "ibkrv2stg-ownertrial-catalog-another-r"):
            replica = reader.CatalogLogScope(fixtures.binding(), group, "catalog",
                target.replica_scope.start_utc, target.replica_scope.end_utc)
            self.error("logs_scope_invalid", family.CatalogFamilyScope, replica, target.execution_name)

    def test_exact_scope_query_types_reject_subclasses_and_arbitrary_objects(self):
        class SubScope(family.CatalogFamilyScope):
            pass
        class SubQuery(family.CatalogFamilyQuery):
            pass
        target = scope()
        self.error("logs_scope_invalid", SubScope, target.replica_scope, target.execution_name)
        self.error("logs_query_invalid", SubQuery, target)
        for value in (None, {}, target.replica_scope, "foreign"):
            self.error("logs_scope_invalid", family.CatalogFamilyQuery, value)
        query = family.CatalogFamilyQuery(target)
        for value in (None, {}, query.text, reader.CatalogQuery(target.replica_scope, "inventory")):
            self.error("logs_query_invalid", family.validate_response, fixtures.table([]), value)

    def test_forged_scopes_and_queries_revalidate_before_any_effect(self):
        for field, value in (("execution_name", "foreign"), ("replica_scope", {})):
            target = scope()
            query = family.CatalogFamilyQuery(target)
            object.__setattr__(target, field, value)
            self.error("logs_scope_invalid", lambda: query.text)
            self.error("logs_scope_invalid", family.validate_response, fixtures.table([]), query)
            family_call, captured_call = Mock(), Mock()
            self.error("logs_scope_invalid", family.collect_evidence, family_call, captured_call, target)
            family_call.assert_not_called()
            captured_call.assert_not_called()
            credential, session = fixtures.Credential(), fixtures.Session([])
            self.error("logs_scope_invalid", family.FamilyLogsTransport(credential, session), query)
            self.assertEqual(credential.calls, [])
            self.assertEqual(session.calls, [])

    def test_forged_nested_slots_never_run_user_scalar_hooks(self):
        calls = []
        class Hostile:
            def __eq__(self, _other):
                calls.append("equality")
                raise AssertionError("scalar hook")
            def __hash__(self):
                calls.append("hash")
                raise AssertionError("scalar hook")
            def __deepcopy__(self, _memo):
                calls.append("deepcopy")
                raise AssertionError("scalar hook")
            def __str__(self):
                calls.append("string")
                raise AssertionError("scalar hook")
        locations = [("scope", "execution_name"), ("scope", "replica_scope")]
        locations += [("replica", name) for name in ("log_group", "container_name", "start_utc", "end_utc", "binding")]
        locations += [("binding", name) for name in codec.BINDING_FIELDS]
        for location, field in locations:
            with self.subTest(location=location, field=field):
                target = scope()
                query = family.CatalogFamilyQuery(target)
                item = target if location == "scope" else target.replica_scope if location == "replica" else target.replica_scope.binding
                object.__setattr__(item, field, Hostile())
                self.error("logs_scope_invalid", lambda: query.text)
                self.error("logs_scope_invalid", family.validate_response, fixtures.table([]), query)
                family_call, captured_call = Mock(), Mock()
                self.error("logs_scope_invalid", family.collect_evidence, family_call, captured_call, target)
                family_call.assert_not_called()
                captured_call.assert_not_called()
                credential, session = fixtures.Credential(), fixtures.Session([])
                self.error("logs_scope_invalid", family.FamilyLogsTransport(credential, session), query)
                self.assertEqual(credential.calls, [])
                self.assertEqual(session.calls, [])
        self.assertEqual(calls, [])


class ResponseTests(FamilyErrors):
    def setUp(self):
        self.fixture = FamilyFixture()
        self.query = family.CatalogFamilyQuery(self.fixture.scope)
        self.valid = fixtures.table(self.fixture.before)

    def test_exact_header_schema_preserves_every_row_and_duplicate(self):
        value = fixtures.table([self.fixture.before[0], self.fixture.before[0]])
        rows = family.validate_response(value, self.query)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], rows[1])
        self.assertEqual(tuple(rows[0]), tuple(name for name, _kind in fixtures.HEADER_COLUMNS))

    def test_errors_partial_continuation_truncation_extra_tables_and_non200_deny(self):
        for name, value in (("error", {}), ("partialError", {}), ("nextLink", "https://evil.invalid"),
            ("truncated", False), ("statistics", {}), ("status", "success")):
            changed = deepcopy(self.valid)
            changed[name] = value
            self.error("logs_response_invalid", family.validate_response, changed, self.query)
        changed = deepcopy(self.valid)
        changed["tables"].append(deepcopy(changed["tables"][0]))
        self.error("logs_response_invalid", family.validate_response, changed, self.query)
        for status in (201, 202, 206, 302, True, "200"):
            self.error("logs_response_invalid", family.validate_response, self.valid, self.query, http_status=status)

    def test_exact_schema_scalar_types_caps_and_utf8_are_bounded(self):
        mutations = [lambda value: value["tables"][0].update(name="Other"),
            lambda value: value["tables"][0].update(nextLink="ignored"),
            lambda value: value["tables"][0]["columns"][0].update(type="dynamic"),
            lambda value: value["tables"][0]["columns"].reverse(),
            lambda value: value["tables"][0]["columns"].append({"name": "Extra", "type": "string"}),
            lambda value: value["tables"][0]["rows"][0].pop(),
            lambda value: value["tables"][0]["rows"][0].__setitem__(6, True),
            lambda value: value["tables"][0]["rows"][0].__setitem__(0, None),
            lambda value: value["tables"][0].update(rows=[self.fixture.before[0]] * 1026)]
        for mutate in mutations:
            changed = deepcopy(self.valid)
            mutate(changed)
            self.error("logs_response_invalid", family.validate_response, changed, self.query)
        for value in ("x" * 8193, "\ud800" * 8193, "\ud800", "\u4e00" * 3000):
            changed = deepcopy(self.valid)
            changed["tables"][0]["rows"][0][4] = value
            self.error("logs_response_invalid", family.validate_response, changed, self.query)


class AssemblyTests(FamilyErrors):
    def test_complete_family_encloses_actual_captured_assembly_and_independent_hash(self):
        fixture = FamilyFixture()
        fixture.before.reverse()
        fixture.captured.inventory_after.reverse()
        fixture.captured.payload_mutation = lambda _query, rows: list(reversed(rows))
        result = fixture.collect()
        self.assertEqual(fixture.events, ["family", "inventory", "payload", "inventory", "family"])
        self.assertEqual(len(fixture.family_requests), 2)
        self.assertTrue(all(type(query) is family.CatalogFamilyQuery for query in fixture.family_requests))
        self.assertEqual(result["captured"]["record"], fixture.captured.record)
        self.assertEqual(result["captured"]["lines"], fixture.captured.lines)
        self.assertEqual(result["family_scope"], asdict(fixture.scope))
        self.assertEqual(result["inventory_before"], sorted(parsed_rows(fixture.before), key=canonical))
        self.assertEqual(result["inventory_after"], sorted(parsed_rows(fixture.after), key=canonical))
        expected = hashlib.sha256(canonical(sorted(parsed_rows(fixture.after), key=canonical))).hexdigest()
        self.assertEqual(result["inventory_sha256"], expected)
        self.assertEqual(result["captured"]["header_inventory_sha256"], expected)
        self.assertIs(result["observed_execution_family_protocol_complete"], True)
        for flag in ("platform_replacement_excluded", "zero_log_replica_absence_verified", "late_delivery_excluded"):
            self.assertIs(result[flag], False)

    def test_canary_family_uses_its_exact_binding(self):
        fixture = FamilyFixture("canary")
        result = fixture.collect()
        self.assertEqual(result["captured"]["record"], fixture.captured.record)
        self.assertEqual(result["family_scope"]["replica_scope"]["binding"]["purpose"], "canary")

    def test_extra_replica_container_and_protocol_version_deny_before_payload(self):
        for index, value in ((0, scope().execution_name + "-second-replica"),
            (0, "ibkrv2stg-ownertrial-catalog-other-replica"), (1, "unexpected-sidecar"),
            (3, "OWNER_TASK_CATALOG_CHUNK_V2 "), (3, "OWNER_TASK_CATALOG_CHUNK_V1 extra")):
            fixture = FamilyFixture()
            extra = deepcopy(fixture.before[0])
            extra[index] = value
            fixture.before.append(extra)
            self.denies_before_captured(fixture)

    def test_every_binding_header_field_wrong_extra_unknown_or_duplicate_json_denies(self):
        changes = {"purpose": "canary", "profile_key": "foreign", "job_resource_id": fixtures.CANARY,
            "operation_id": "11111111-1111-4111-8111-111111111111", "source_sha256": "b" * 64,
            "kind": "unknown", "extra": True, "data_b64": "unexpected"}
        for key, value in changes.items():
            fixture = FamilyFixture()
            header = json.loads(fixture.before[0][4])
            header[key] = value
            fixture.before[0][4] = json.dumps(header)
            self.denies_before_captured(fixture)
        for text in ('{"kind":"chunk","kind":"chunk"}', "{", "NaN", "null", "[]"):
            fixture = FamilyFixture()
            fixture.before[0][4] = text
            self.denies_before_captured(fixture)

    def test_duplicate_missing_and_wrong_index_rows_deny_before_captured(self):
        changes = (lambda rows: rows.append(deepcopy(rows[0])),
            lambda rows: rows.append(deepcopy(rows[-1])), lambda rows: rows.pop(0),
            lambda rows: rows.pop(-1), lambda rows: rows.clear(),
            lambda rows: rows[0].__setitem__(4, rows[0][4].replace('"index":0', '"index":1')))
        for change in changes:
            fixture = FamilyFixture()
            change(fixture.before)
            self.denies_before_captured(fixture)

    def test_header_scope_time_hash_byte_length_and_utf8_errors_deny(self):
        for index, value in ((0, "foreign"), (1, "foreign"), (2, "2026-10-04T15:00:00Z"),
            (2, "2026-10-04T13:59:59Z"), (2, "bad-date"), (5, "A" * 64), (5, "b" * 63),
            (6, 0), (6, -1), (6, 8193)):
            fixture = FamilyFixture()
            fixture.before[0][index] = value
            self.denies_before_captured(fixture)
        fixture = FamilyFixture()
        fixture.before[0][4] = "x" * 8193
        self.denies_before_captured(fixture, "logs_response_invalid")

    def test_partial_error_extra_table_and_rowcap_deny_before_captured(self):
        mutations = (lambda value: {**value, "error": {"code": "PartialError"}},
            lambda value: {**value, "nextLink": "https://evil.invalid"},
            lambda value: {"tables": value["tables"] * 2})
        for mutate in mutations:
            fixture = FamilyFixture()
            fixture.response_mutation = mutate
            self.denies_before_captured(fixture, "logs_response_invalid")
        fixture = FamilyFixture()
        fixture.before = [fixture.before[0]] * 1026
        self.denies_before_captured(fixture, "logs_response_invalid")

    def test_family_hash_must_match_captured_inventory_even_when_both_families_stable(self):
        for index, value in ((2, "2026-10-04T14:31:00Z"), (5, "b" * 64), (6, 50)):
            fixture = FamilyFixture()
            fixture.before[0][index] = value
            fixture.after[0][index] = value
            self.error("logs_inventory_drift", fixture.collect)
            self.assertTrue(fixture.captured.requests)

    def test_second_family_complete_inventory_denies_extra_missing_and_drift(self):
        changes = (lambda rows: rows.append(deepcopy(rows[0])), lambda rows: rows.pop(0),
            lambda rows: rows[0].__setitem__(0, scope().execution_name + "-new-replica"),
            lambda rows: rows[0].__setitem__(1, "sidecar"),
            lambda rows: rows[0].__setitem__(3, "OWNER_TASK_CATALOG_CHUNK_V2 "),
            lambda rows: rows[0].__setitem__(5, "b" * 64))
        for change in changes:
            fixture = FamilyFixture()
            change(fixture.after)
            with self.assertRaises(reader.CatalogLogsError):
                fixture.collect()
            self.assertEqual(fixture.events, ["family", "inventory", "payload", "inventory", "family"])

    def test_captured_missing_payload_or_replica_inventory_drift_is_not_masked(self):
        for change in (lambda fixture: setattr(fixture.captured, "payload_mutation", lambda _query, rows: rows[:-1]),
            lambda fixture: fixture.captured.inventory_after[0].__setitem__(5, "b" * 64)):
            fixture = FamilyFixture()
            change(fixture)
            with self.assertRaises(reader.CatalogLogsError):
                fixture.collect()
            self.assertEqual(len(fixture.family_requests), 1)

    def test_sanitized_family_query_failure_never_retries_or_exposes_provider_text(self):
        for stage in (1, 2):
            fixture = FamilyFixture()
            calls = []
            def failure(query):
                calls.append(query)
                if len(calls) == stage:
                    raise RuntimeError("SYNTHETIC_PROVIDER_CREDENTIAL")
                return fixture.family_query(query)
            self.error("logs_record_invalid", family.collect_evidence, failure, fixture.captured_query, fixture.scope)
            self.assertEqual(len(calls), stage)
            if stage == 1:
                self.assertEqual(fixture.captured.requests, [])


class TransportTests(FamilyErrors):
    def valid(self):
        fixture = FamilyFixture()
        return fixtures.table(fixture.before)

    def test_fixed_readonly_post_audience_timespan_no_redirects_timeout_stream_and_cache(self):
        responses = [fixtures.Response(self.valid()), fixtures.Response(self.valid())]
        credential, session = fixtures.Credential(), fixtures.Session(responses)
        transport = family.FamilyLogsTransport(credential, session)
        query = family.CatalogFamilyQuery(scope())
        with patch.object(reader.time, "time", return_value=1000):
            self.assertEqual(transport(query), self.valid())
            transport(query)
        self.assertEqual(credential.calls, ["https://api.loganalytics.io/.default"])
        for args, kwargs in session.calls:
            self.assertEqual(args, ("POST", "https://api.loganalytics.io/v1/workspaces/5264a124-10cd-45f6-93a5-6d65c37e9bfb/query"))
            self.assertEqual(kwargs["json"], {"query": query.text, "timespan": "2026-10-04T14:00:00Z/2026-10-04T15:00:00Z"})
            self.assertIs(kwargs["allow_redirects"], False)
            self.assertIs(kwargs["stream"], True)
            self.assertEqual(kwargs["timeout"], (5, 20))
        self.assertTrue(all(response.closed for response in responses))
        self.assertTrue(all(response.chunk_sizes == [16384] for response in responses))

    def test_default_session_zero_retries_and_no_request_or_auth_discovery(self):
        fake = SimpleNamespace(mount=Mock(), request=Mock())
        credential = fixtures.Credential()
        with patch.object(reader.requests, "Session", return_value=fake):
            family.FamilyLogsTransport(credential)
        fake.request.assert_not_called()
        self.assertEqual(credential.calls, [])
        self.assertEqual(fake.mount.call_args.args[0], "https://")
        self.assertEqual(fake.mount.call_args.args[1].max_retries.total, 0)

    def test_bad_query_rejects_before_token_or_request(self):
        credential, session = fixtures.Credential(), fixtures.Session([])
        transport = family.FamilyLogsTransport(credential, session)
        for value in (None, {}, "ContainerAppConsoleLogs_CL", reader.CatalogQuery(scope().replica_scope, "inventory")):
            self.error("logs_query_invalid", transport, value)
        query = family.CatalogFamilyQuery(scope())
        object.__setattr__(query, "scope", {})
        self.error("logs_scope_invalid", transport, query)
        self.assertEqual(credential.calls, [])
        self.assertEqual(session.calls, [])

    def test_redirect_partial_bad_json_duplicate_nan_truncated_oversized_and_timeout_no_retry(self):
        malformed = deepcopy(self.valid())
        malformed["error"] = {"code": "PartialError"}
        cases = (fixtures.Response(self.valid(), status=302), fixtures.Response(self.valid(), status=206),
            fixtures.Response(malformed), fixtures.Response(content=b'{"tables":[],"tables":[]}'),
            fixtures.Response(content=b'{"tables":NaN}'), fixtures.Response(content=b'{'),
            fixtures.Response(content=b"x" * (2 * 1024 * 1024 + 1)),
            RuntimeError("SYNTHETIC_PROVIDER_SECRET"))
        for response in cases:
            credential, session = fixtures.Credential(), fixtures.Session([response])
            self.error("logs_transport_failed", family.FamilyLogsTransport(credential, session),
                family.CatalogFamilyQuery(scope()))
            self.assertEqual(len(session.calls), 1)
            if isinstance(response, fixtures.Response):
                self.assertTrue(response.closed)

    def test_complete_bytes_still_require_full_primary_result_contract(self):
        for mutate in (lambda value: value.update(truncated=False),
            lambda value: value["tables"][0].update(rows=[self.valid()["tables"][0]["rows"][0]] * 1026),
            lambda value: value["tables"].append(deepcopy(value["tables"][0]))):
            value = self.valid()
            mutate(value)
            response = fixtures.Response(value)
            session = fixtures.Session([response])
            self.error("logs_transport_failed", family.FamilyLogsTransport(fixtures.Credential(), session),
                family.CatalogFamilyQuery(scope()))
            self.assertTrue(response.closed)
            self.assertEqual(len(session.calls), 1)


class RehearsalBoundaryTests(unittest.TestCase):
    @staticmethod
    def require(condition, code):
        if not condition:
            raise ValueError(code)

    def test_exact707_to711_source_extension_keeps_every_frozen_byte_digest(self):
        previous = {"frozen/%03d.py" % index: "a" * 64 for index in range(707)}
        allowed = ("scripts/read_owner_task_catalog_family.py", "tests/test_owner_task_catalog_family.py",
            "scripts/run_owner_task_isolated_provider_observation.py", "tests/test_owner_task_isolated_provider_observation.py")
        self.assertEqual(family.ADDITIONS, allowed)
        current = {**previous, **{name: "b" * 64 for name in allowed}}
        inert = SimpleNamespace(require=self.require)
        with patch.object(family, "rehearsal", return_value=inert):
            family.validate_sources(previous, current)
            changes = (lambda value: value.update({"frozen/000.py": "c" * 64}),
                lambda value: value.update({"another.py": "d" * 64}),
                lambda value: value.pop(allowed[0]),
                lambda value: value.update({allowed[0]: "A" * 64}),
                lambda value: value.update({allowed[0]: True}))
            for mutate in changes:
                changed = deepcopy(current)
                mutate(changed)
                with self.assertRaisesRegex(ValueError, "^family_only_four_new_sources_allowed$"):
                    family.validate_sources(previous, changed)
            with self.assertRaisesRegex(ValueError, "^family_only_four_new_sources_allowed$"):
                family.validate_sources(dict(list(previous.items())[:-1]), current)
            with self.assertRaisesRegex(ValueError, "^family_only_four_new_sources_allowed$"):
                family.validate_sources(None, current)

    def test_only_four_exact_new_protected_record_names_are_registered(self):
        names = ("preflight.json", "inventory-before.json", "inventory-after.json", "acceptance.json")
        self.assertEqual(family.NEW_NAMES, names)
        with patch.object(family, "rehearsal", return_value=SimpleNamespace(require=self.require)):
            for name in names:
                self.assertEqual(family.record_kind(name), "catalog-family-" + name.removesuffix(".json"))
            for name in (None, True, "../acceptance.json", "inventory-before.json\n", "part-000.json",
                "start-intent.json", "start-ack.json", "install-intent.json", "unknown.json"):
                with self.assertRaisesRegex(ValueError, "^family_record_name_unregistered$"):
                    family.record_kind(name)

    def test_import_is_inert_with_credentials_network_and_artifact_writes_disabled(self):
        path = str(Path(family.__file__).resolve())
        with patch.object(reader.requests, "Session", side_effect=AssertionError("network constructor")), \
             patch("azure.identity.AzureCliCredential", side_effect=AssertionError("credential discovery")), \
             patch("subprocess.run", side_effect=AssertionError("subprocess dispatch")), \
             patch.object(Path, "mkdir", side_effect=AssertionError("artifact directory write")), \
             patch.object(Path, "read_bytes", side_effect=AssertionError("protected artifact read")), \
             patch.object(Path, "write_bytes", side_effect=AssertionError("artifact write")):
            imported = runpy.run_path(path, run_name="family_inert_import_oracle")
        self.assertIn("CatalogFamilyScope", imported)
        self.assertIn("main", imported)

    def test_cli_rejects_dispatch_stages_before_host_auth_or_arm_calls(self):
        host, arm = Mock(), Mock()
        inert = SimpleNamespace(prior=SimpleNamespace(host=SimpleNamespace(checked_owner=host)), ReadOnlyArm=arm)
        for stage in ("start", "install", "sql", "deploy", "cleanup"):
            with patch.object(family, "rehearsal", return_value=inert), \
                 patch.object(sys, "argv", ["read_owner_task_catalog_family.py", stage]), \
                 patch.object(sys, "stderr", io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    family.main()
            self.assertEqual(caught.exception.code, 2)
        host.assert_not_called()
        arm.assert_not_called()

    def test_cli_fixed_prepare_collect_verify_dispatch_uses_readonly_arm_and_injected_credentials(self):
        for stage in ("prepare", "collect", "verify"):
            host, arm_constructor, credential_constructor = Mock(), Mock(return_value=object()), Mock(return_value=object())
            inert = SimpleNamespace(prior=SimpleNamespace(host=SimpleNamespace(checked_owner=host),
                AzureCliCredential=credential_constructor), ReadOnlyArm=arm_constructor)
            fake_folder = SimpleNamespace(mkdir=Mock())
            prepared = {"status": "prepared", "local_checks": {"test_count": 2040}, "protected_receipt": {"sha256": "a" * 64}}
            stdout = io.StringIO()
            with patch.object(family, "rehearsal", return_value=inert), \
                 patch.object(family, "folder", return_value=fake_folder), \
                 patch.object(family, "prepare", return_value=prepared) as prepare, \
                 patch.object(family, "collect", return_value={"status": "readback"}) as collect, \
                 patch.object(family, "FamilyLogsTransport", return_value=object()) as broad, \
                 patch.object(reader, "LogsTransport", return_value=object()) as captured, \
                 patch.object(sys, "argv", ["read_owner_task_catalog_family.py", stage]), \
                 patch.object(sys, "stdout", stdout):
                family.main()
                host.assert_called_once_with()
                arm_constructor.assert_called_once_with()
                if stage == "verify":
                    fake_folder.mkdir.assert_not_called()
                else:
                    fake_folder.mkdir.assert_called_once_with(parents=True, exist_ok=True)
                if stage == "prepare":
                    prepare.assert_called_once_with(arm_constructor.return_value)
                    collect.assert_not_called()
                    credential_constructor.assert_not_called()
                    broad.assert_not_called()
                    captured.assert_not_called()
                    self.assertEqual(json.loads(stdout.getvalue())["tests"], 2040)
                else:
                    prepare.assert_not_called()
                    credential_constructor.assert_called_once_with()
                    broad.assert_called_once_with(credential_constructor.return_value)
                    captured.assert_called_once_with(credential_constructor.return_value)
                    collect.assert_called_once_with(arm_constructor.return_value, broad.return_value,
                        captured.return_value, verify_only=stage == "verify")
                    self.assertEqual(json.loads(stdout.getvalue()), {"status": "readback"})


class RehearsalOrchestrationTests(unittest.TestCase):
    """Small mocked retention oracles, not replacements for the old 4MiB tests.

    Live family queries and decoding remain real. Only legacy protected evidence,
    ARM/runtime guards and protected storage are replaced by in-memory fixtures.
    Synthetic receipts here make no authentication or live canary claims.
    """

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.r = family.rehearsal()
        self.live = FamilyFixture("canary")
        self.arm = object()
        self.runtime = {"python": "synthetic-fixed-runtime", "requirements_lock_sha256": "a" * 64}
        previous = {"frozen/%03d.py" % index: "a" * 64 for index in range(707)}
        self.current = {**previous, **{name: "b" * 64 for name in family.ADDITIONS}}
        self.records = {"history.json": self.seal({"synthetic": True}, "history")}
        self.old_preflight = self.seal({"sources": previous}, "previous-preflight")
        self.old_acceptance = self.seal({"retained": True}, "previous-acceptance")
        self.observed = {"mapping": {"log_group": self.live.scope.replica_scope.log_group},
            "scope_start": self.live.scope.replica_scope.start_utc,
            "scope_end": self.live.scope.replica_scope.end_utc,
            "execution": {"status": "Succeeded", "execution": self.live.scope.execution_name}}
        self.stack.enter_context(patch.object(self.r.prior, "binding", return_value=self.live.scope.replica_scope.binding))
        self.stack.enter_context(patch.object(self.r.prior, "CONTAINER", self.live.scope.replica_scope.container_name))
        self.stack.enter_context(patch.object(self.r.prior.host, "runtime_descriptor", return_value=deepcopy(self.runtime)))
        self.stack.enter_context(patch.object(self.r, "sources", return_value=deepcopy(self.current)))
        self.qa = self.stack.enter_context(patch.object(self.r.prior.qa, "run_local_checks",
            return_value=readback_fixtures.checks(2040)))
        self.guard = self.stack.enter_context(patch.object(family, "guards",
            side_effect=lambda *_args: (deepcopy(self.current), deepcopy(self.observed))))
        self.previous = self.stack.enter_context(patch.object(family, "previous_evidence",
            return_value=(self.records, self.old_preflight, self.old_acceptance, self.live.captured.lines)))
        # Only this legacy maximum-size qualification is mocked. Existing frozen
        # readback tests exercise the actual complete 4MiB protected wire codec.
        self.full_wire_gate = self.stack.enter_context(patch.object(self.r.prior, "complete_evidence", return_value=self.r.RECORD_SHA))
        self.prepared = {"status": "prepared_readonly_observed_execution_family_rehearsal",
            "sources": deepcopy(self.current), "runtime": deepcopy(self.runtime),
            "local_checks": readback_fixtures.checks(2040), "allowed_additions": list(family.ADDITIONS),
            "binding": asdict(self.live.scope.replica_scope.binding), "observed": deepcopy(self.observed),
            "history_receipts": self.r.history_receipts(self.records),
            "previous_preflight_receipt": self.old_preflight["protected_receipt"],
            "previous_acceptance_receipt": self.old_acceptance["protected_receipt"],
            "sql_calls": 0, "model_calls": 0, **{key: False for key in self.r.FALSE_FLAGS},
            "zero_log_replica_absence_verified": False, "late_delivery_excluded": False}
        self.new = {"preflight.json": self.seal(self.prepared, "preflight")}
        self.reads, self.writes = [], []
        self.stack.enter_context(patch.object(family, "folder", return_value=readback_fixtures.MemoryPath(self.new)))
        self.loader = self.stack.enter_context(patch.object(family, "load", side_effect=self.load))
        self.saver = self.stack.enter_context(patch.object(family, "save", side_effect=self.save))

    @staticmethod
    def seal(value, name):
        raw = deepcopy(value)
        raw["protected_receipt"] = {"blob": "synthetic/" + name,
            "sha256": hashlib.sha256(canonical(value)).hexdigest(),
            "version_id": "2026-10-04T14:00:00.1234567Z", "protected_readback_verified": True}
        return raw

    def load(self, name):
        self.reads.append(name)
        raw = self.new[name]
        if type(raw) is not dict or "protected_receipt" not in raw:
            raise ValueError("synthetic_partial_marker_inspection_only")
        return deepcopy(raw), self.r.body(deepcopy(raw))

    def save(self, value, name):
        if name in self.new:
            raise AssertionError("synthetic_overwrite_forbidden")
        self.writes.append(name)
        self.new[name] = self.seal(value, name)
        return deepcopy(self.new[name])

    def collect(self, verify_only=False):
        return family.collect(self.arm, self.live.family_query, self.live.captured_query, verify_only=verify_only)

    def test_collect_retains_both_complete_inventories_and_acceptance_after_final_guard(self):
        result = self.collect()
        self.assertEqual(self.writes, ["inventory-before.json", "inventory-after.json", "acceptance.json"])
        self.assertEqual(self.live.events, ["family", "inventory", "payload", "inventory", "family"])
        self.assertEqual(self.guard.call_count, 2)
        self.assertEqual(result["status"], "observed_execution_family_transport_rehearsal_retained")
        accepted = self.r.body(self.new["acceptance.json"])
        self.assertEqual(accepted["frame_count"], 2)
        self.assertEqual(accepted["inventory_receipts"], {
            stage: self.new["inventory-" + stage + ".json"]["protected_receipt"] for stage in ("before", "after")})
        self.assertIs(accepted["observed_execution_family_protocol_complete"], True)
        for flag in (*self.r.FALSE_FLAGS, "zero_log_replica_absence_verified", "late_delivery_excluded"):
            self.assertIs(accepted[flag], False)

    def test_both_inventory_values_are_capped_before_the_first_protected_save(self):
        original = family.inventory_value
        def oversized_second(rows, stage, prepared, evidence):
            value = original(rows, stage, prepared, evidence)
            if stage == "after":
                value["oversize"] = "x" * (self.r.MAX_BYTES + 1)
            return value
        with patch.object(family, "inventory_value", side_effect=oversized_second) as values:
            with self.assertRaisesRegex(ValueError, "^family_inventory_oversized$"):
                self.collect()
        self.assertEqual([call.args[1] for call in values.call_args_list], ["before", "after"])
        self.saver.assert_not_called()
        self.assertEqual(self.writes, [])
        self.assertEqual(set(self.new), {"preflight.json"})

    def test_verify_rereads_previous_live_inventories_and_acceptance_and_never_saves(self):
        self.collect()
        saved = deepcopy(self.new)
        self.reads.clear()
        self.writes.clear()
        self.previous.reset_mock()
        self.guard.reset_mock()
        self.saver.reset_mock()
        # A fresh small live fixture may return the same multiset in a new order.
        self.live = FamilyFixture("canary")
        self.live.before.reverse()
        self.live.after.reverse()
        result = self.collect(verify_only=True)
        self.previous.assert_called_once_with()
        self.assertEqual(self.reads, ["preflight.json", "inventory-before.json", "inventory-after.json", "acceptance.json"])
        self.assertEqual(self.live.events, ["family", "inventory", "payload", "inventory", "family"])
        self.assertEqual(self.guard.call_count, 2)
        self.saver.assert_not_called()
        self.assertEqual(self.writes, [])
        self.assertEqual(self.new, saved)
        self.assertEqual(result["status"], "independent_observed_execution_family_rehearsal_verified")

    def test_verify_retained_inventory_and_final_acceptance_mismatch_deny_without_save(self):
        self.collect()
        retained = deepcopy(self.new)
        for name, field, value in (("inventory-before.json", "inventory_sha256", "f" * 64),
            ("inventory-after.json", "rows", []), ("acceptance.json", "inventory_sha256", "f" * 64),
            ("acceptance.json", "late_delivery_excluded", True)):
            self.new.clear()
            self.new.update(deepcopy(retained))
            self.new[name][field] = value
            self.live = FamilyFixture("canary")
            self.saver.reset_mock()
            with self.assertRaises(ValueError):
                self.collect(verify_only=True)
            self.saver.assert_not_called()

    def test_final_guard_drift_can_retain_inventory_but_prevents_acceptance(self):
        changed = deepcopy(self.observed)
        changed["execution"]["status"] = "Running"
        self.guard.side_effect = [(deepcopy(self.current), deepcopy(self.observed)),
            (deepcopy(self.current), changed)]
        with self.assertRaisesRegex(ValueError, "^family_exact_frozen_preflight_required$"):
            self.collect()
        self.assertEqual(self.writes, ["inventory-before.json", "inventory-after.json"])
        self.assertNotIn("acceptance.json", self.new)

    def test_frozen_exact_current_qa_refs_sources_runtime_binding_flags_and_counts(self):
        self.assertEqual(family.frozen(self.prepared, self.records, self.old_preflight, self.old_acceptance, self.arm), self.observed)
        mutations = (lambda value: value["sources"].update({"frozen/000.py": "c" * 64}),
            lambda value: value["runtime"].update(python="different"),
            lambda value: value["binding"].update(source_sha256="c" * 64),
            lambda value: value["history_receipts"].clear(),
            lambda value: value["previous_preflight_receipt"].update(sha256="c" * 64),
            lambda value: value["previous_acceptance_receipt"].update(sha256="c" * 64),
            lambda value: value["allowed_additions"].pop(),
            lambda value: value.update(zero_log_replica_absence_verified=True),
            lambda value: value.update(late_delivery_excluded=True),
            lambda value: value.update(platform_replacement_excluded=0),
            lambda value: value.update(sql_calls=False), lambda value: value.update(model_calls=0.0),
            lambda value: value["local_checks"].update(test_count=2003, passed_test_count=2003),
            lambda value: value["local_checks"].update(test_count=2040.0),
            lambda value: value["local_checks"].update(unittest_exit_code=False),
            lambda value: value["local_checks"].update(live_sql_tests_passed=True),
            lambda value: value["local_checks"].pop("repository_policy"))
        for mutate in mutations:
            changed = deepcopy(self.prepared)
            mutate(changed)
            with self.assertRaises(ValueError):
                family.frozen(changed, self.records, self.old_preflight, self.old_acceptance, self.arm)

    def test_consumed_partial_markers_are_never_overwritten_or_retried(self):
        for name in ("preflight.json", "inventory-before.json", "inventory-after.json", "acceptance.json"):
            self.new.clear()
            self.new["preflight.json"] = self.seal(self.prepared, "preflight")
            self.new[name] = {"partial": True}
            self.live = FamilyFixture("canary")
            self.saver.reset_mock()
            with self.assertRaises((ValueError, KeyError)):
                self.collect(verify_only=name == "acceptance.json")
            self.assertEqual(self.new[name], {"partial": True})
            self.assertNotIn(name, [call.args[1] for call in self.saver.call_args_list])
            self.assertLessEqual(len(self.live.family_requests), 2)

    def test_prepare_does_not_rerun_qa_or_overwrite_a_consumed_partial_preflight(self):
        self.new["preflight.json"] = {"partial": True}
        with self.assertRaisesRegex(ValueError, "^family_preflight_consumed_inspect_only$"):
            family.prepare(self.arm)
        self.previous.assert_not_called()
        self.qa.assert_not_called()
        self.saver.assert_not_called()

    def test_collect_denies_existing_or_partial_acceptance_before_reads_live_queries_or_saves(self):
        for marker in ({"partial": True}, self.seal({"status": "already-consumed"}, "acceptance")):
            self.new["acceptance.json"] = deepcopy(marker)
            with self.assertRaisesRegex(ValueError, "^family_acceptance_exists_use_verify$"):
                self.collect()
            self.loader.assert_not_called()
            self.previous.assert_not_called()
            self.guard.assert_not_called()
            self.saver.assert_not_called()
            self.assertEqual(self.live.family_requests, [])
            self.assertEqual(self.live.captured.requests, [])
            self.assertEqual(self.new["acceptance.json"], marker)

    def test_inventory_stage_requires_exact_native_string_without_user_scalar_hooks(self):
        calls = []
        class Hostile(str):
            def __eq__(self, _other):
                calls.append("equality")
                raise AssertionError("scalar hook")
        evidence = self.live.collect()
        raw = self.new["preflight.json"]
        for stage in (None, False, 1, "before\n", "unknown", Hostile("before")):
            with self.assertRaisesRegex(ValueError, "^family_inventory_stage_unregistered$"):
                family.inventory_value([], stage, raw, evidence)
        self.assertEqual(calls, [])
        for stage in ("before", "after"):
            value = family.inventory_value(evidence["inventory_" + stage], stage, raw, evidence)
            self.assertEqual(value["stage"], stage)
            self.assertIs(value["zero_log_replica_absence_verified"], False)
            self.assertIs(value["platform_replacement_excluded"], False)
            self.assertIs(value["late_delivery_excluded"], False)

    def test_prepare_runs_fresh_current_qa_between_unchanged_guards_before_saving(self):
        self.new.clear()
        result = family.prepare(self.arm)
        self.previous.assert_called_once_with()
        self.qa.assert_called_once_with()
        self.assertEqual(self.guard.call_count, 2)
        self.assertEqual(self.writes, ["preflight.json"])
        self.assertEqual(self.r.body(result), self.prepared)
        self.assertEqual(result["local_checks"]["test_count"], 2040)


if __name__ == "__main__":
    unittest.main()
