"""Offline trusted provider worker contracts, NOT live SQL/credential isolation."""
import ast
import copy
from dataclasses import asdict
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import run_owner_task_isolated_provider_observation as worker
import test_owner_task_isolated_provider as fixtures


PASSWORD = "Aa9!" + "x" * 64  # Synthetic only; never a cloud credential.


def secret(password=PASSWORD):
    return "postgresql://" + worker.registry.ADMIN + ":" + quote(password, safe="") + "@" + worker.registry.HOST \
        + ":5432/postgres?sslmode=verify-full&sslrootcert=" + quote(worker.CA_PATH, safe="")


def binding():
    return asdict(worker.codec.Binding("provider", worker.registry.PROFILE_KEY, worker.registry.JOB_ID,
        worker.OPERATION_ID, "a" * 64))


class SecretOption:
    keyword = b"password"

    @property
    def val(self):
        raise AssertionError("The worker must never inspect password metadata")


class Raw(fixtures.Raw):
    def __init__(self, events=None):
        super().__init__()
        self.events = [] if events is None else events
        self.settings = copy.deepcopy(worker.SETTINGS)
        self.pgconn.info = [SimpleNamespace(keyword=key.encode("ascii"), val=value.encode("ascii"))
            for key, value in dict(host=worker.registry.HOST, dbname="postgres", user=worker.registry.ADMIN,
                port="5432", sslmode="verify-full", sslrootcert=worker.CA_PATH,
                connect_timeout=str(worker.CONNECT_SECONDS), options=worker.OPTIONS,
                application_name=worker.APPLICATION_NAME).items()] + [SecretOption()]
        self.closed = False
        self.close_error = False

    def execute(self, text):
        self.events.append(("sql", text))
        if text == worker.SETTINGS_SQL:
            self.calls.append(text)
            if self.on_execute is not None:
                self.on_execute(self, text)
            value = copy.deepcopy(self.settings)
            return SimpleNamespace(fetchone=lambda: value)
        return super().execute(text)

    def close(self):
        self.events.append(("close", None))
        self.closed = True
        if self.close_error:
            raise RuntimeError(secret())


class WorkerTests(unittest.TestCase):
    def run_worker(self, raw=None, **kwargs):
        raw = Raw() if raw is None else raw
        received = []

        def connect(**parameters):
            received.append(parameters)
            raw.events.append(("connect", None))
            return raw

        with patch.object(worker, "require_ca_bundle", return_value=None):
            lines = worker.observe(binding(), secret(), connect=connect, **kwargs)
        return raw, received, lines

    def test_canonical_secret_and_explicit_fixed_connection(self):
        raw, received, lines = self.run_worker()
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0], dict(host=worker.registry.HOST, user=worker.registry.ADMIN,
            password=PASSWORD, dbname="postgres", port="5432", sslmode="verify-full",
            sslrootcert=worker.CA_PATH, autocommit=True, connect_timeout=20, row_factory=worker.dict_row,
            options=worker.OPTIONS, application_name=worker.APPLICATION_NAME))
        self.assertTrue(raw.closed)
        self.assertEqual(raw.calls[0], worker.collector.PATH_SQL)
        self.assertTrue(all(text.startswith("SELECT ") for text in raw.calls))
        for text in raw.calls:
            self.assertNotRegex(text, r"\b(?:INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|GRANT|REVOKE|SET|set_config)\b")
        record = worker.codec.decode_record(lines, worker.fixed_binding(binding()))
        self.assertEqual(record["catalog"], raw.catalog)
        for key in ("provider_semantics_verified", "restricted_setup_authorized", "fresh_restricted_login_verified",
                    "model_credential_isolation_verified", "authenticated_owner_verified", "protected_retention_verified"):
            self.assertIs(record[key], False)
        self.assertNotIn(PASSWORD, "".join(lines))
        self.assertNotIn(secret(), "".join(lines))

    def test_fixed_operation_profile_job_purpose_and_native_fields(self):
        for key, bad in (("purpose", "canary"), ("profile_key", "other"),
            ("job_resource_id", worker.codec.CANARY_JOB_ID),
            ("operation_id", "b86898e4-09db-4e79-92e3-114b2ebc209e"),
            ("operation_id", "4482B962-755D-4EDA-987D-D954FC288143"),
            ("source_sha256", "A" * 64), ("source_sha256", True)):
            values = binding(); values[key] = bad
            with self.subTest(key=key, bad=bad):
                with self.assertRaises(worker.ProviderWorkerError) as error:
                    worker.fixed_binding(values)
                self.assertEqual("provider_worker_binding_invalid", str(error.exception))
        for values in ({**binding(), "extra": "no"}, None, [], worker.fixed_binding(binding())):
            with self.assertRaises(worker.ProviderWorkerError):
                worker.fixed_binding(values)
        result = worker.fixed_binding(binding())
        values = binding(); copied = worker.fixed_binding(values); values["operation_id"] = "not_registered"
        self.assertEqual(result, copied)

    def test_dsn_mutations_duplicates_extra_options_and_weakening_rejected(self):
        good = secret()
        bad_values = [good.replace("postgresql://", "postgres://"),
            good.replace(worker.registry.HOST, "ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com"),
            good.replace(worker.registry.HOST, worker.registry.HOST + ".evil"),
            good.replace(worker.registry.ADMIN, "other_admin"),
            good.replace(":5432/postgres", ":5433/postgres"),
            good.replace(":5432/postgres", "/postgres"),
            good.replace("/postgres?", "/" + worker.registry.DATABASE + "?"),
            good.replace("verify-full", "require"), good.replace("verify-full", "disable"),
            good + "&options=-c%20default_transaction_read_only%3Doff",
            good + "&service=production", good + "&password=override",
            good + "&sslmode=verify-full", good + "&sslrootcert=%2Fother.pem",
            good.replace("sslmode=", "%73slmode="),
            good.replace("%2Fetc%2F", "%2fetc%2f"), good.replace("%21", "!"),
            good.replace("%21", "%2"), good.replace("%21", "%FF"),
            good.replace("%2Fetc%2Fssl%2Fcerts%2Fca-certificates.crt", "%2Ftmp%2Fca.crt"),
            good + "#fragment", good + "\n", secret("weak"),
            "host=" + worker.registry.HOST + " user=" + worker.registry.ADMIN,
            True, None, {}, good + "x" * 4096]
        for value in bad_values:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(worker.ProviderWorkerError) as error:
                    worker.validate_admin_secret(value)
                self.assertEqual("provider_worker_secret_invalid", str(error.exception))
                self.assertNotIn(PASSWORD, str(error.exception))

    def test_url_str_subclass_and_unusual_separator_rejected(self):
        class Custom(str):
            pass
        for bad in (Custom(secret()), secret().replace("?sslmode=", "?sslmode=&sslmode="),
            secret().replace("&sslrootcert=", ";sslrootcert="),
            secret().replace(worker.registry.HOST, worker.registry.HOST.upper())):
            with self.assertRaises(worker.ProviderWorkerError):
                worker.validate_admin_secret(bad)

    def test_validation_failures_do_not_connect_emit_or_hold(self):
        connect = unittest.mock.Mock()
        emit = unittest.mock.Mock()
        hold = unittest.mock.Mock()
        for values, dsn, ca in ((dict(binding(), purpose="canary"), secret(), None),
            (binding(), secret().replace("verify-full", "disable"), None),
            (binding(), secret(), worker.ProviderWorkerError("provider_worker_ca_missing"))):
            with patch.object(worker, "require_ca_bundle", side_effect=ca):
                with self.assertRaises(worker.ProviderWorkerError):
                    worker.main(values, dsn, connect=connect, emit=emit, hold=hold)
        connect.assert_not_called(); emit.assert_not_called(); hold.assert_not_called()

    def test_ca_bundle_missing_or_symlink_rejected(self):
        for is_file, is_symlink in ((False, False), (True, True)):
            with patch.object(worker.Path, "is_file", return_value=is_file), \
                    patch.object(worker.Path, "is_symlink", return_value=is_symlink):
                with self.assertRaises(worker.ProviderWorkerError) as error:
                    worker.require_ca_bundle()
                self.assertEqual("provider_worker_ca_missing", error.exception.code)
        with patch.object(worker.Path, "is_file", return_value=True), \
                patch.object(worker.Path, "is_symlink", return_value=False):
            worker.require_ca_bundle()

    def test_wrong_connection_metadata_rejected_before_any_sql(self):
        for key, bad in (("host", "other"), ("dbname", "template1"), ("user", "other"),
                         ("user", True), ("transaction_status", 1), ("transaction_status", False)):
            raw = Raw(); setattr(raw.info, key, bad)
            with self.assertRaises(worker.ProviderWorkerError):
                self.run_worker(raw)
            self.assertEqual([], raw.calls)
            self.assertTrue(raw.closed)
        for bad in (False, 1):
            raw = Raw(); raw.autocommit = bad
            with self.assertRaises(worker.ProviderWorkerError):
                self.run_worker(raw)
            self.assertEqual([], raw.calls)
            self.assertTrue(raw.closed)

    def test_tls_path_timeouts_options_and_duplicate_parameter_rejected_before_sql(self):
        for key in ("host", "dbname", "user", "port", "sslmode", "sslrootcert", "connect_timeout",
                    "options", "application_name"):
            raw = Raw()
            for option in raw.pgconn.info:
                if option.keyword == key.encode("ascii"):
                    option.val = b"wrong"
            with self.assertRaises(worker.ProviderWorkerError):
                self.run_worker(raw)
            self.assertEqual([], raw.calls)
            self.assertTrue(raw.closed)
        for change in (lambda raw: raw.pgconn.info.pop(0),
            lambda raw: raw.pgconn.info.append(raw.pgconn.info[0]),
            lambda raw: setattr(raw.pgconn, "ssl_in_use", False),
            lambda raw: setattr(raw.pgconn, "ssl_in_use", 1)):
            raw = Raw(); change(raw)
            with self.assertRaises(worker.ProviderWorkerError):
                self.run_worker(raw)
            self.assertEqual([], raw.calls)

    def test_session_settings_rejected_and_connection_closed(self):
        for key in worker.SETTINGS:
            raw = Raw(); raw.settings[key] = "off" if key.endswith("readonly") else "wrong"
            with self.assertRaises(worker.ProviderWorkerError) as error:
                self.run_worker(raw)
            self.assertEqual("provider_worker_session_unsafe", error.exception.code)
            self.assertEqual([worker.collector.PATH_SQL, worker.SETTINGS_SQL], raw.calls)
            self.assertTrue(raw.closed)
        raw = Raw(); raw.settings["extra"] = "no"
        with self.assertRaises(worker.ProviderWorkerError):
            self.run_worker(raw)

    def test_identity_or_catalog_drift_withholds_complete_protocol(self):
        for change in (lambda raw: raw.identity.update(role="other"),
            lambda raw: raw.catalog["routines"][0].update(raw_definition="forbidden"),
            lambda raw: raw.path.update(temporary_schema=9000)):
            raw = Raw(); change(raw)
            emitted = []
            with patch.object(worker, "require_ca_bundle", return_value=None):
                with self.assertRaises(worker.ProviderWorkerError):
                    worker.main(binding(), secret(), connect=lambda **_: raw,
                        emit=lambda line, **_: emitted.append(line), hold=lambda _: None)
            self.assertEqual([], emitted)
            self.assertTrue(raw.closed)

    def test_final_startup_or_transport_drift_withholds_record(self):
        for change in (lambda raw: raw.settings.update(readonly="off"),
            lambda raw: setattr(raw.pgconn, "ssl_in_use", False),
            lambda raw: setattr(raw.info, "transaction_status", 2)):
            raw = Raw()
            def on_execute(current, text):
                if text == worker.collector.SPECS["routine_acls"][3]:
                    change(current)
            raw.on_execute = on_execute
            with self.assertRaises(worker.ProviderWorkerError):
                self.run_worker(raw)
            self.assertTrue(raw.closed)

    def test_connect_and_query_exception_text_is_sanitized_without_frames(self):
        for stage in ("connect", "query"):
            raw = Raw()
            def fail(*args, **kwargs):
                raise RuntimeError("SECRET " + PASSWORD + " " + secret())
            if stage == "query":
                raw.on_execute = fail
            emitted = []
            with patch.object(worker, "require_ca_bundle", return_value=None):
                with self.assertRaises(worker.ProviderWorkerError) as error:
                    worker.main(binding(), secret(), connect=fail if stage == "connect" else lambda **_: raw,
                        emit=lambda line, **_: emitted.append(line), hold=lambda _: None)
            self.assertEqual("provider_worker_observation_failed", str(error.exception))
            self.assertNotIn(PASSWORD, str(error.exception))
            self.assertEqual([], emitted)
            self.assertEqual(raw.closed, stage == "query")

    def test_credential_in_untrusted_catalog_metadata_never_emitted(self):
        raw = Raw(); raw.catalog["routines"][0]["identity_arguments"] = PASSWORD
        emitted = []
        with patch.object(worker, "require_ca_bundle", return_value=None):
            with self.assertRaises(worker.ProviderWorkerError) as error:
                worker.main(binding(), secret(), connect=lambda **_: raw,
                    emit=lambda line, **_: emitted.append(line), hold=lambda _: None)
        self.assertEqual("provider_worker_observation_failed", str(error.exception))
        self.assertTrue(raw.closed); self.assertEqual([], emitted)

    def test_close_failure_withholds_all_protocol_frames(self):
        raw = Raw(); raw.close_error = True
        emitted = []; held = []
        with patch.object(worker, "require_ca_bundle", return_value=None):
            with self.assertRaises(worker.ProviderWorkerError) as error:
                worker.main(binding(), secret(), connect=lambda **_: raw,
                    emit=lambda line, **_: emitted.append(line), hold=lambda seconds: held.append(seconds))
        self.assertEqual("provider_worker_close_failed", str(error.exception))
        self.assertTrue(raw.closed); self.assertEqual([], emitted); self.assertEqual([], held)

    def test_success_all_frames_emitted_only_after_close_then_fixed_hold(self):
        events = []; raw = Raw(events); emitted = []
        def emit(line, *, flush):
            self.assertIs(flush, True); self.assertTrue(raw.closed)
            emitted.append(line); events.append(("emit", None))
        with patch.object(worker, "require_ca_bundle", return_value=None):
            result = worker.main(binding(), secret(), connect=lambda **_: raw,
                emit=emit, hold=lambda seconds: events.append(("hold", seconds)))
        self.assertLess(next(n for n, event in enumerate(events) if event[0] == "close"),
                        next(n for n, event in enumerate(events) if event[0] == "emit"))
        self.assertEqual(("hold", 300), events[-1])
        manifest = worker.codec.parse_line(emitted[-1], worker.fixed_binding(binding()))
        self.assertEqual("manifest", manifest["kind"])
        self.assertEqual(result["line_count"], len(emitted))
        self.assertEqual(result["record_sha256"], manifest["record_sha256"])
        self.assertEqual(raw.catalog, worker.codec.decode_record(emitted, worker.fixed_binding(binding()))["catalog"])
        for key in ("provider_semantics_verified", "restricted_login_verified", "credential_isolation_verified",
                    "protected_retention_verified"):
            self.assertIs(result[key], False)

    def test_full_large_catalog_not_sampled_and_oversize_emits_nothing(self):
        raw = Raw()
        raw.catalog["routines"] = [fixtures.routine(1000 + n, 20) for n in range(3250)]
        raw.catalog["routine_acls"] = [fixtures.acl(1000 + n, "EXECUTE", grantee)
            for n in range(3250) for grantee in (0, 11)]
        _, _, lines = self.run_worker(raw)
        record = worker.codec.decode_record(lines, worker.fixed_binding(binding()))
        self.assertGreater(len(worker.codec.canonical(record)), 2 * 1024 * 1024)
        self.assertEqual(3250, record["catalog_counts"]["routines"])
        self.assertEqual(6500, record["catalog_counts"]["routine_acls"])
        raw = Raw()
        raw.catalog["routines"] = [dict(fixtures.routine(1000 + n, 20), identity_arguments="x" * 2048)
            for n in range(4096)]
        raw.catalog["routine_acls"] = [fixtures.acl(1000 + n, "EXECUTE") for n in range(4096)]
        emitted = []
        with patch.object(worker, "require_ca_bundle", return_value=None):
            with self.assertRaises(worker.ProviderWorkerError):
                worker.main(binding(), secret(), connect=lambda **_: raw,
                    emit=lambda line, **_: emitted.append(line), hold=lambda _: None)
        self.assertEqual([], emitted); self.assertTrue(raw.closed)

    def test_output_failure_is_sanitized_and_not_retried(self):
        raw = Raw(); calls = []
        def emit(line, **kwargs):
            calls.append(line)
            raise RuntimeError(secret())
        with patch.object(worker, "require_ca_bundle", return_value=None):
            with self.assertRaises(worker.ProviderWorkerError) as error:
                worker.main(binding(), secret(), connect=lambda **_: raw, emit=emit, hold=lambda _: None)
        self.assertEqual("provider_worker_emit_failed", str(error.exception))
        self.assertEqual(1, len(calls)); self.assertTrue(raw.closed)

    def test_worker_has_no_cli_environment_secret_retrieval_or_write_sql(self):
        source = Path(worker.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertFalse(any(isinstance(node, ast.If) for node in tree.body))
        self.assertNotRegex(source, r"os\.environ|argparse|AzureCliCredential|DefaultAzureCredential|requests\.|subprocess\.")
        self.assertNotIn("psycopg.connect(", source)
        self.assertNotIn("option.password", source)
        self.assertNotIn("get_parameters()", source.split("def transport_context", 1)[0])
        self.assertEqual(300, worker.HOLD_SECONDS)
        self.assertEqual(("src/ibkr_etl/owner_codex_isolated_target.py",
            "src/ibkr_etl/owner_codex_catalog_transport.py",
            "scripts/inspect_owner_task_isolated_provider.py",
            "scripts/run_owner_task_isolated_provider_observation.py"), worker.WORKER_SOURCE_PATHS)


if __name__ == "__main__":
    unittest.main()
