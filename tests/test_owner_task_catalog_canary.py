"""Local synthetic worker tests; not Azure delivery or credential isolation."""
import ast
from dataclasses import asdict
import hashlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_owner_task_catalog_canary as worker
from ibkr_etl import owner_codex_catalog_transport as codec
from ibkr_etl.owner_codex_isolated_target import BASE, PROFILE_KEY


def binding(purpose="canary"):
    job = "ibkrv2stg-ownertrial-log-canary" if purpose == "canary" else "ibkrv2stg-ownertrial-catalog"
    return codec.Binding(purpose=purpose, profile_key=PROFILE_KEY,
        job_resource_id=BASE + "Microsoft.App/jobs/" + job,
        operation_id="b86898e4-09db-4e79-92e3-114b2ebc209e", source_sha256="a" * 64)


class CatalogCanaryTests(unittest.TestCase):
    def test_record_is_full_4_mib_not_compressed_or_sampled(self):
        value = worker.synthetic_record(binding())
        self.assertEqual(4 * 1024 * 1024, len(codec.canonical(value)))
        self.assertGreater(len(codec.canonical(value)), 2 * 1024 * 1024)
        self.assertEqual(asdict(binding()), value["binding"])
        self.assertIs(value["credentials_supplied"], False)
        for name in ("sql_calls", "source_rows_read", "source_rows_written", "agents_activated"):
            self.assertEqual(0, value[name])

    def test_record_is_deterministic_and_source_bound(self):
        first = codec.canonical(worker.synthetic_record(binding()))
        self.assertEqual(first, codec.canonical(worker.synthetic_record(binding())))
        different = codec.Binding(**{**asdict(binding()), "source_sha256": "b" * 64})
        self.assertNotEqual(hashlib.sha256(first).digest(),
            hashlib.sha256(codec.canonical(worker.synthetic_record(different))).digest())

    def test_wire_has_all_1024_chunks_and_one_manifest(self):
        value = worker.synthetic_record(binding())
        lines = codec.encode_record(value, binding())
        self.assertEqual(1025, len(lines))
        self.assertEqual(value, codec.decode_record(lines, binding()))
        self.assertTrue(all(len(line.encode("utf-8")) <= 8192 for line in lines))

    def test_only_canary_binding_is_accepted(self):
        for bad in (binding("provider"), {}, None, object()):
            with self.subTest(bad=type(bad).__name__), self.assertRaises(ValueError):
                worker.synthetic_record(bad)

    def test_stdout_emission_has_bounded_hold_and_no_trailer(self):
        stream = io.StringIO()
        with patch("sys.stdout", stream), patch.object(worker.time, "sleep") as sleep:
            worker.main(asdict(binding()))
        lines = stream.getvalue().splitlines()
        self.assertEqual(1025, len(lines))
        self.assertEqual(worker.synthetic_record(binding()), codec.decode_record(lines, binding()))
        sleep.assert_called_once_with(300)

    def test_worker_has_no_environment_network_sql_or_file_routes(self):
        source = Path(worker.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imports |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        self.assertEqual({"dataclasses", "hashlib", "time", "ibkr_etl.owner_codex_catalog_transport"}, imports)
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {"open", "eval", "exec", "__import__"} for node in ast.walk(tree)))
        self.assertFalse(any(isinstance(node, ast.Attribute) and node.attr in
            {"environ", "getenv", "read_text", "read_bytes", "write_text", "write_bytes", "connect"}
            for node in ast.walk(tree)))

    def test_forged_binding_never_calls_attacker_hooks(self):
        class Hook:
            def __deepcopy__(self, memo):
                self.fail()
            def __eq__(self, other):
                self.fail()
            def __hash__(self):
                self.fail()
            def fail(self):
                raise AssertionError("attacker hook invoked")
        bad = binding()
        object.__setattr__(bad, "purpose", Hook())
        with self.assertRaises(ValueError):
            worker.synthetic_record(bad)


if __name__ == "__main__":
    unittest.main()
