"""Offline runtime contracts; synthetic metadata is not image qualification."""
import ast
import copy
import hashlib
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import owner_task_provider_runtime as runtime


def descriptor():
    return dict(schema_version=1, observation="image_runtime_metadata_only", python_implementation="CPython",
        python_version="3.12.11", system="Linux", machine="x86_64", pointer_bits=64, effective_uid=10001,
        effective_gid=10001, psycopg_version="3.3.6", psycopg_binary_version="3.3.6", pq_implementation="binary",
        libpq_version=180000, libpq_build_version=180000, ca_path=runtime.CA_PATH, ca_bytes=1234,
        ca_sha256="a" * 64, ca_certificates=145, pg_environment_absent=True,
        **{key: False for key in runtime.FALSE_FLAGS}, **{key: 0 for key in runtime.ZERO_COUNTS})


class RuntimeTests(unittest.TestCase):
    def test_native_descriptor_copy_no_activation(self):
        value = descriptor(); copied = runtime.validate_descriptor(value)
        self.assertEqual(copied, value); self.assertIsNot(copied, value)
        value["effective_uid"] = 0
        self.assertEqual(10001, copied["effective_uid"])
        for name in runtime.FALSE_FLAGS:
            self.assertIs(copied[name], False)

    def test_all_fields_native_and_schema_exact(self):
        for key in descriptor():
            value = descriptor()
            value[key] = True if type(value[key]) is int else 1 if type(value[key]) is bool else None
            with self.subTest(key=key), self.assertRaises(runtime.RuntimeCheckError):
                runtime.validate_descriptor(value)
            value = descriptor(); value.pop(key)
            with self.assertRaises(runtime.RuntimeCheckError):
                runtime.validate_descriptor(value)
        for bad in (None, [], dict(descriptor(), extra=False)):
            with self.assertRaises(runtime.RuntimeCheckError):
                runtime.validate_descriptor(bad)

    def test_string_and_dict_subclasses_rejected(self):
        class CustomDict(dict): pass
        class CustomString(str): pass
        with self.assertRaises(runtime.RuntimeCheckError):
            runtime.validate_descriptor(CustomDict(descriptor()))
        for key, expected in descriptor().items():
            if type(expected) is str:
                value = descriptor(); value[key] = CustomString(expected)
                with self.subTest(key=key), self.assertRaises(runtime.RuntimeCheckError):
                    runtime.validate_descriptor(value)

    def test_matching_unsupported_psycopg_versions_rejected(self):
        for version in ("3.0.0", "3.1.20"):
            value = descriptor(); value.update(psycopg_version=version, psycopg_binary_version=version)
            with self.subTest(version=version), self.assertRaises(runtime.RuntimeCheckError):
                runtime.validate_descriptor(value)

    def test_incompatible_runtime_and_certificate_metadata_rejected(self):
        cases = (("schema_version", 2), ("observation", "live_sql"), ("python_implementation", "PyPy"),
            ("python_version", "3.14.0"), ("python_version", "3.12.1rc1"), ("system", "Windows"),
            ("machine", "aarch64"), ("pointer_bits", 32), ("effective_uid", 0), ("effective_gid", 0),
            ("psycopg_version", "3.3.6+unreviewed"), ("psycopg_binary_version", "3.3.5"),
            ("pq_implementation", "python"), ("libpq_version", 159999), ("libpq_version", 200000),
            ("libpq_build_version", 170000), ("ca_path", "/tmp/ca.crt"), ("ca_bytes", 0),
            ("ca_bytes", runtime.MAX_CA_BYTES + 1), ("ca_sha256", "A" * 64), ("ca_certificates", 0),
            ("ca_certificates", 4097), ("pg_environment_absent", False))
        for key, bad in cases:
            value = descriptor(); value[key] = bad
            with self.subTest(key=key, bad=bad), self.assertRaises(runtime.RuntimeCheckError):
                runtime.validate_descriptor(value)

    def test_pg_and_tls_overrides_rejected_not_silently_removed(self):
        for key in ("PGHOST", "PGPASSWORD", "PGSERVICE", "PGSERVICEFILE", "PGHOSTADDR", "PGSSLCERT",
            "PGSSLKEY", "pgoptions", "PgTargetSessionAttrs", "PGUNREVIEWED_FUTURE_OPTION",
            "SSL_CERT_FILE", "ssl_cert_dir", "OPENSSL_CONF", "OPENSSL_MODULES"):
            env = {key: "synthetic-private-value"}
            with self.subTest(key=key), self.assertRaises(runtime.RuntimeCheckError) as error:
                runtime.reject_connection_environment(env)
            self.assertEqual("provider_runtime_connection_override", str(error.exception))
            self.assertEqual(env, {key: "synthetic-private-value"})
            self.assertNotIn("synthetic-private-value", str(error.exception))

    def test_environment_values_never_accessed_or_copied(self):
        class AmbientKeys:
            def keys(self): return ["PATH", "OWNERTRIAL_ADMIN_URL"]
            def __getitem__(self, key): raise AssertionError("credential value access")
            def __iter__(self): raise AssertionError("environment copy")
        with patch.object(runtime.os, "environ", AmbientKeys()):
            runtime.reject_connection_environment()
        runtime.reject_connection_environment({"OWNERTRIAL_ADMIN_URL": object(), "PATH": object()})
        for bad in ([], True, {None: "no"}, {"": "no"}, {"\n": "no"}, {"é": "no"}, {"x" * 513: "no"}):
            with self.assertRaises(runtime.RuntimeCheckError):
                runtime.reject_connection_environment(bad)

    def test_runtime_guard_precedes_library_ca_or_secret_work(self):
        with patch.object(runtime, "ca_descriptor") as ca:
            with self.assertRaises(runtime.RuntimeCheckError) as error:
                runtime.check_runtime({"PGHOSTADDR": "synthetic-private"})
            self.assertEqual("provider_runtime_connection_override", str(error.exception))
            ca.assert_not_called()
        with patch.object(runtime.sys, "platform", "win32"), patch.object(runtime, "ca_descriptor") as ca:
            with self.assertRaises(runtime.RuntimeCheckError) as error:
                runtime.check_runtime({})
            self.assertEqual("provider_runtime_platform_invalid", str(error.exception))
            ca.assert_not_called()

    def file_metadata(self, **changes):
        values = dict(st_mode=stat.S_IFREG | 0o644, st_dev=1, st_ino=2, st_size=12, st_mtime_ns=3, st_ctime_ns=4)
        values.update(changes); return SimpleNamespace(**values)

    def test_ca_reads_captured_bytes_fixed_path_no_follow_and_exact_hash(self):
        raw = b"test pem ca\n"; before = self.file_metadata(st_size=len(raw))
        context = Mock(); context.verify_mode = runtime.ssl.CERT_REQUIRED; context.check_hostname = True
        context.cert_store_stats.return_value = {"x509_ca": 1}
        with patch.object(runtime.Path, "lstat", return_value=before), \
            patch.object(runtime.os, "O_NOFOLLOW", 131072, create=True), \
            patch.object(runtime.os, "O_NONBLOCK", 2048, create=True), \
            patch.object(runtime.os, "open", return_value=7) as opened, \
            patch.object(runtime.os, "fstat", return_value=before), \
            patch.object(runtime.os, "read", side_effect=[raw, b""]), \
            patch.object(runtime.os, "close") as closed, \
            patch.object(runtime.ssl, "SSLContext", return_value=context):
            result = runtime.ca_descriptor()
        opened.assert_called_once_with(runtime.CA_PATH, runtime.os.O_RDONLY | 131072 | 2048)
        closed.assert_called_once_with(7)
        context.load_verify_locations.assert_called_once_with(cadata=raw.decode("ascii"))
        self.assertEqual(result, dict(ca_path=runtime.CA_PATH, ca_bytes=len(raw),
            ca_sha256=hashlib.sha256(raw).hexdigest(), ca_certificates=1))

    def test_ca_symlink_fifo_empty_and_oversize_fail_before_open(self):
        for before in (self.file_metadata(st_mode=stat.S_IFLNK), self.file_metadata(st_mode=stat.S_IFIFO),
            self.file_metadata(st_size=0), self.file_metadata(st_size=runtime.MAX_CA_BYTES + 1)):
            with patch.object(runtime.Path, "lstat", return_value=before), patch.object(runtime.os, "open") as opened:
                with self.assertRaises(runtime.RuntimeCheckError): runtime.ca_descriptor()
                opened.assert_not_called()

    def test_ca_replacement_drift_parse_error_and_close_error_sanitized(self):
        raw = b"test pem ca\n"; before = self.file_metadata(st_size=len(raw))
        for stage in ("opened_inode", "final_inode", "changed_size", "parse", "close"):
            context = Mock(); context.verify_mode = runtime.ssl.CERT_REQUIRED; context.check_hostname = True
            context.cert_store_stats.return_value = {"x509_ca": 1}
            if stage == "parse": context.load_verify_locations.side_effect = RuntimeError("synthetic-private")
            metadata = self.file_metadata(st_size=len(raw), st_ino=99) if stage == "opened_inode" else before
            final = self.file_metadata(st_size=len(raw), st_ino=99) if stage == "final_inode" else before
            if stage == "changed_size": raw_value = b"short"
            else: raw_value = raw
            with patch.object(runtime.Path, "lstat", side_effect=[before, final]), \
                patch.object(runtime.os, "O_NOFOLLOW", 131072, create=True), \
                patch.object(runtime.os, "O_NONBLOCK", 2048, create=True), \
                patch.object(runtime.os, "open", return_value=7), \
                patch.object(runtime.os, "fstat", return_value=metadata), \
                patch.object(runtime.os, "read", side_effect=[raw_value, b""]), \
                patch.object(runtime.os, "close", side_effect=RuntimeError("synthetic-private") if stage == "close" else None) as closed, \
                patch.object(runtime.ssl, "SSLContext", return_value=context):
                with self.subTest(stage=stage), self.assertRaises(runtime.RuntimeCheckError) as error:
                    runtime.ca_descriptor()
                self.assertEqual("provider_runtime_ca_invalid", str(error.exception))
                self.assertNotIn("synthetic-private", str(error.exception)); closed.assert_called_once_with(7)

    def test_library_compatibility_is_observed_but_connection_never_called(self):
        import psycopg
        with patch.object(runtime.sys, "implementation", SimpleNamespace(name="cpython")), \
            patch.object(runtime.sys, "version_info", (3, 12, 11)), patch.object(runtime.sys, "platform", "linux"), \
            patch.object(runtime.platform, "python_implementation", return_value="CPython"), \
            patch.object(runtime.platform, "python_version", return_value="3.12.11"), \
            patch.object(runtime.platform, "system", return_value="Linux"), \
            patch.object(runtime.platform, "machine", return_value="x86_64"), \
            patch.object(runtime.os, "geteuid", return_value=10001, create=True), \
            patch.object(runtime.os, "getegid", return_value=10001, create=True), \
            patch.object(psycopg, "__version__", "3.3.6"), \
            patch.object(runtime.metadata, "version", return_value="3.3.6"), \
            patch.object(psycopg.pq, "__impl__", "binary"), patch.object(psycopg.pq, "version", return_value=180000), \
            patch.object(psycopg.pq, "__build_version__", 180000), \
            patch.object(runtime, "ca_descriptor", return_value={key: descriptor()[key] for key in
                ("ca_path", "ca_bytes", "ca_sha256", "ca_certificates")}), patch.object(psycopg, "connect") as connect:
            value = runtime.check_runtime({})
            self.assertEqual(descriptor(), value); connect.assert_not_called()

    def test_module_has_no_import_time_action_cli_connector_or_environment_values(self):
        source = Path(runtime.__file__).read_text(encoding="utf-8"); tree = ast.parse(source)
        self.assertFalse(any(isinstance(node, ast.If) for node in tree.body))
        self.assertNotRegex(source, r"psycopg\.connect\(|subprocess\.|requests\.|AzureCliCredential|os\.environ\[|dict\(os\.environ\)")
        self.assertEqual("/etc/ssl/certs/ca-certificates.crt", runtime.CA_PATH)


if __name__ == "__main__":
    unittest.main()
