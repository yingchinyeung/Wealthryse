"""Real local process tests, NOT Azure/SQL/authentication/power-loss evidence."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from ibkr_etl.owner_codex_trial_journal import DurableTrialJournal, key_for_slot, MAX_RECORD_BYTES
from ibkr_etl.owner_codex_trial_journal_local import LocalJournalError, LocalTrialJournalBackend, ROOT_PREFIX
from ibkr_etl.owner_codex_trial_lifecycle import (MIGRATION_SHA256, REQUIRED_SOURCE_FILES,
    LifecycleError, Phase, PhaseReservation, ProtectedReceipt, ReviewedRegistration,
    TrialMode, TrialPlan, _record, digest)


def synthetic_plan(operation):
    files = tuple((p, MIGRATION_SHA256 if p.endswith("059_owner_codex_tasks.sql") else digest(p))
                  for p in REQUIRED_SOURCE_FILES)
    registration = ReviewedRegistration(files, digest(files), "synthetic_installer",
        "00000000-0000-0000-0000-000000000302")
    return TrialPlan(operation, registration, digest("synthetic-baseline"), digest("synthetic-boundary"),
                     TrialMode.SYNTHETIC)


def child():
    """Only fixed test actions; arguments never select a command or filename."""
    mode, root, reviewed_parent, operation, gate = sys.argv[1:]
    backend = LocalTrialJournalBackend(root, reviewed_parent=reviewed_parent)
    plan = synthetic_plan(operation)
    journal = DurableTrialJournal(backend)
    if gate != "none":
        deadline = time.monotonic() + 10
        while not Path(gate).exists():
            if time.monotonic() > deadline:
                raise ValueError("synthetic start gate timed out")
            time.sleep(0.01)
    if mode == "backend_race":
        created = backend.create_if_absent(key_for_slot(1), operation.encode())
        print(json.dumps({"created": created, "operation": operation}), flush=True)
        return
    if mode == "reserve_race":
        reservation = journal.reserve_phase(plan, Phase.SETUP, (), None)
        print(json.dumps({"reserved": reservation is not None, "operation": operation}), flush=True)
        return
    if mode == "lost_reservation":
        original = backend.create_if_absent
        def lost(key, value):
            created = original(key, value)
            if created:
                os._exit(37)  # Complete bytes committed; producer never sees a response.
            return created
        backend.create_if_absent = lost
        journal.reserve_phase(plan, Phase.SETUP, (), None)
        raise ValueError("fault did not execute")
    if mode == "lost_claim":
        reservation = journal.reserve_phase(plan, Phase.SETUP, (), None)
        receipt = ProtectedReceipt(digest(_record(plan, reservation, "intent")), digest("synthetic-reference"))
        original = backend.create_if_absent
        def lost(key, value):
            created = original(key, value)
            if created:
                os._exit(38)
            return created
        backend.create_if_absent = lost
        journal.claim_phase(reservation, receipt)
        raise ValueError("fault did not execute")
    if mode == "restart":
        reservation = journal.reserve_phase(plan, Phase.SETUP, (), None)
        print(json.dumps({"reserved": reservation is not None}), flush=True)
        return
    if mode == "restart_claim":
        reservation = PhaseReservation(plan.plan_sha256, plan.target.scope_sha256,
            Phase.SETUP, 1, digest([]), None)
        receipt = ProtectedReceipt(digest(_record(plan, reservation, "intent")), digest("synthetic-reference"))
        try:
            journal.claim_phase(reservation, receipt)
        except LifecycleError as error:
            print(json.dumps({"refused": error.code == "claim_unverified"}), flush=True)
            return
        raise ValueError("persisted evidence recreated an execution permit")
    if mode == "reject_fifo":
        refused = []
        for operation in (lambda: backend.read(key_for_slot(1)),
                          lambda: backend.create_if_absent(key_for_slot(1), b"new")):
            try:
                operation()
            except LocalJournalError:
                refused.append(True)
            else:
                refused.append(False)
        print(json.dumps({"refused": refused}), flush=True)
        return
    if mode == "partial_staging":
        # Marker/gate files are separate from the registered journal root.
        original = os.write
        calls = 0
        def partial(fd, data):
            nonlocal calls
            calls += 1
            if calls == 1:
                count = original(fd, data[:3])
                Path(gate + ".staged").touch()
                deadline = time.monotonic() + 10
                while not Path(gate + ".publish").exists():
                    if time.monotonic() > deadline:
                        raise ValueError("synthetic publish gate timed out")
                    time.sleep(0.01)
                return count
            return original(fd, data)
        os.write = partial
        created = backend.create_if_absent(key_for_slot(1), b"complete-synthetic-record")
        print(json.dumps({"created": created}), flush=True)
        return
    raise ValueError("unregistered synthetic child mode")


class LocalJournalProcessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix=ROOT_PREFIX)
        self.root = Path(self.directory.name)
        self.backend = LocalTrialJournalBackend(self.root, reviewed_parent=self.root.parent)
        self.operation = str(uuid.uuid4())
        self.processes = []
        self.gates = tempfile.TemporaryDirectory(prefix="owner-codex-test-gates-")

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()  # This exact test-owned subprocess only; never a generic PID scan.
            process.communicate(timeout=10)
        self.directory.cleanup()
        self.gates.cleanup()

    def launch(self, mode, operation=None, gate="none"):
        project = Path(__file__).resolve().parents[1]
        environment = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "TEMP", "TMP") if k in os.environ}
        environment["PYTHONPATH"] = os.pathsep.join((str(project / "src"), str(project / "tests")))
        # Direct base interpreter avoids Windows venv launcher child indirection.
        process = subprocess.Popen([sys._base_executable, "-c",
            "from test_owner_codex_trial_journal_process import child; child()", mode,
            str(self.root), str(self.root.parent), operation or self.operation, str(gate)], env=environment,
            cwd=self.gates.name, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(process)
        return process

    def result(self, process, exit_code=0):
        stdout, stderr = process.communicate(timeout=15)
        self.assertEqual(exit_code, process.returncode, stderr)
        self.assertEqual("", stderr)
        return None if not stdout else json.loads(stdout)

    def test_construction_inert_and_read_absent(self):
        self.assertEqual([], list(self.root.iterdir()))
        self.assertIsNone(self.backend.read(key_for_slot(1)))
        self.assertEqual([], list(self.root.iterdir()))
        self.assertIs(self.backend.supports_live_execution, False)
        self.assertEqual("local_process_restart_only", self.backend.durability_scope)

    def test_explicit_parent_never_probes_or_discovers_temp_directory(self):
        with patch("tempfile.gettempdir", side_effect=AssertionError("implicit tempfile discovery")):
            backend = LocalTrialJournalBackend(self.root, reviewed_parent=self.root.parent)
            self.assertIsNone(backend.read(key_for_slot(1)))
        self.assertEqual([], list(self.root.iterdir()))

    def test_create_read_and_no_overwrite(self):
        key = key_for_slot(1)
        self.assertIs(self.backend.create_if_absent(key, b"synthetic-A"), True)
        self.assertIs(self.backend.create_if_absent(key, b"synthetic-B"), False)
        self.assertEqual(b"synthetic-A", self.backend.read(key))
        self.assertEqual(["01"], [p.name for p in self.root.iterdir()])

    def test_multiple_actual_processes_one_full_record_wins(self):
        gate = Path(self.gates.name) / "start"
        processes = [self.launch("backend_race", str(uuid.uuid4()), gate) for _ in range(4)]
        gate.touch()
        results = [self.result(p) for p in processes]
        winners = [r for r in results if r["created"]]
        self.assertEqual(1, len(winners))
        self.assertEqual(winners[0]["operation"].encode(), self.backend.read(key_for_slot(1)))
        self.assertEqual(["01"], [p.name for p in self.root.iterdir()])

    def test_multiple_actual_processes_one_scope_reservation_across_operation_ids(self):
        gate = Path(self.gates.name) / "start"
        processes = [self.launch("reserve_race", str(uuid.uuid4()), gate) for _ in range(4)]
        gate.touch()
        results = [self.result(p) for p in processes]
        self.assertEqual(1, sum(r["reserved"] for r in results))
        self.assertFalse(self.result(self.launch("restart", str(uuid.uuid4())))["reserved"])

    def test_process_exit_after_reservation_publication_never_reopens_scope(self):
        self.result(self.launch("lost_reservation"), 37)
        self.assertFalse(self.result(self.launch("restart"))["reserved"])
        self.assertFalse(self.result(self.launch("restart", str(uuid.uuid4())))["reserved"])

    def test_process_exit_after_claim_publication_never_reclaims(self):
        self.result(self.launch("lost_claim"), 38)
        self.assertEqual("claimed", DurableTrialJournal(self.backend).snapshot().status)
        self.assertFalse(self.result(self.launch("restart"))["reserved"])
        self.assertFalse(self.result(self.launch("restart", str(uuid.uuid4())))["reserved"])
        self.assertTrue(self.result(self.launch("restart_claim"))["refused"])

    def test_pending_partial_write_is_never_visible_as_final_record(self):
        gate = Path(self.gates.name) / "partial"
        gate.touch()
        process = self.launch("partial_staging", gate=gate)
        deadline = time.monotonic() + 10
        while not Path(str(gate) + ".staged").exists():
            self.assertIsNone(process.poll(), "synthetic writer exited before staging")
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        self.assertIsNone(self.backend.read(key_for_slot(1)))
        Path(str(gate) + ".publish").touch()
        self.assertTrue(self.result(process)["created"])
        self.assertEqual(b"complete-synthetic-record", self.backend.read(key_for_slot(1)))

    def test_bad_keys_and_records_rejected_without_writes(self):
        key = key_for_slot(1)
        for bad in ("01", key + ".json", key + "/../02", key[:-2] + "00", key[:-2] + "11",
                    key[:-2] + "1", key.replace("/01", "/%30%31"), True):
            with self.subTest(key=bad):
                with self.assertRaises(LocalJournalError):
                    self.backend.read(bad)
                with self.assertRaises(LocalJournalError):
                    self.backend.create_if_absent(bad, b"data")
        for bad in (b"", b"x" * (MAX_RECORD_BYTES + 1), "text", bytearray(b"data"), None):
            with self.assertRaises(LocalJournalError):
                self.backend.create_if_absent(key, bad)
        self.assertEqual([], list(self.root.iterdir()))

    def test_missing_relative_and_mismatched_registered_roots_rejected(self):
        for bad in ("relative", self.root / "child", self.root.parent / "unregistered-root",
                    str(self.root.parent / ".." / self.root.parent.name / self.root.name)):
            with self.assertRaises(LocalJournalError):
                LocalTrialJournalBackend(bad, reviewed_parent=self.root.parent)
        for bad in ("relative", self.root, self.root.parent / "unregistered-parent", False):
            with self.assertRaises(LocalJournalError):
                LocalTrialJournalBackend(self.root, reviewed_parent=bad)

    @unittest.skipUnless(os.name == "nt", "Windows drive metadata fixture")
    def test_unc_and_mapped_drive_roots_rejected_before_filesystem_access(self):
        roots = (Path(r"\\synthetic.invalid\share\owner-codex-trial-journal-abcdefgh"),
                 Path(r"Z:\owner-codex-trial-journal-abcdefgh"))
        for root in roots:
            with self.subTest(root=str(root)):
                api = SimpleNamespace(GetDriveTypeW=lambda anchor: 4)  # DRIVE_REMOTE
                with patch("ibkr_etl.owner_codex_trial_journal_local._windows_api", return_value=(api, None)), \
                        patch.object(Path, "lstat", side_effect=AssertionError("network path metadata attempted")) as metadata:
                    with self.assertRaises(LocalJournalError):
                        LocalTrialJournalBackend(root, reviewed_parent=root.parent)
                    metadata.assert_not_called()

    def test_oversized_empty_and_directory_final_entries_fail_closed(self):
        for value in (b"", b"x" * (MAX_RECORD_BYTES + 1)):
            path = self.root / "01"
            path.write_bytes(value)  # Test fixture only, not backend behavior.
            with self.assertRaises(LocalJournalError):
                self.backend.read(key_for_slot(1))
            with self.assertRaises(LocalJournalError):
                self.backend.create_if_absent(key_for_slot(1), b"valid")
            path.unlink()
        (self.root / "01").mkdir()
        with self.assertRaises(LocalJournalError):
            self.backend.read(key_for_slot(1))

    @unittest.skipUnless(os.name == "posix", "POSIX FIFO fixture")
    def test_actual_fifo_entry_is_rejected_without_blocking(self):
        os.mkfifo(self.root / "01", mode=0o600)
        # A regression that blocks opening the FIFO times out this owned child,
        # rather than hanging the test runner; teardown kills that exact child.
        self.assertEqual([True, True], self.result(self.launch("reject_fifo"))["refused"])

    def test_root_identity_change_is_not_followed(self):
        moved = self.root.with_name(self.root.name + "_moved")
        self.root.rename(moved)
        self.root.mkdir(mode=0o700)
        try:
            with self.assertRaises(LocalJournalError):
                self.backend.create_if_absent(key_for_slot(1), b"data")
            self.assertEqual([], list(self.root.iterdir()))
        finally:
            self.root.rmdir()
            moved.rename(self.root)

    def test_symlink_entry_never_followed(self):
        target = Path(self.gates.name) / "outside"
        target.write_bytes(b"outside synthetic bytes")
        try:
            (self.root / "01").symlink_to(target)
        except OSError:
            self.skipTest("OS does not permit unprivileged test symlink creation")
        with self.assertRaises(LocalJournalError):
            self.backend.read(key_for_slot(1))
        with self.assertRaises(LocalJournalError):
            self.backend.create_if_absent(key_for_slot(1), b"new")
        self.assertEqual(b"outside synthetic bytes", target.read_bytes())

    def test_reparse_ancestor_is_rejected_before_read_or_write(self):
        original = Path.lstat
        def reparse(path, *args, **kwargs):
            actual = original(path, *args, **kwargs)
            if path == self.root.parent:
                return SimpleNamespace(st_mode=actual.st_mode, st_file_attributes=0x400)
            return actual
        with patch.object(Path, "lstat", reparse):
            with self.assertRaises(LocalJournalError):
                self.backend.read(key_for_slot(1))
            with self.assertRaises(LocalJournalError):
                self.backend.create_if_absent(key_for_slot(1), b"data")
        self.assertEqual([], list(self.root.iterdir()))

    @unittest.skipUnless(os.name == "nt", "Windows junction fixture")
    def test_actual_windows_junction_entry_is_rejected(self):
        # Junctions need no symbolic-link privilege. Only the two known test
        # temporary directories are involved; nothing outside them is mutated.
        destination = Path(self.gates.name) / "synthetic-junction-target"
        destination.mkdir()
        junction = self.root / "01"
        command = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
        environment = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "TEMP", "TMP") if k in os.environ}
        result = subprocess.run([str(command), "/d", "/c", "mklink", "/J", str(junction), str(destination)],
            capture_output=True, timeout=10, env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(0, result.returncode, "synthetic junction creation failed")
        try:
            with self.assertRaises(LocalJournalError):
                self.backend.read(key_for_slot(1))
            with self.assertRaises(LocalJournalError):
                self.backend.create_if_absent(key_for_slot(1), b"new")
            self.assertEqual([], list(destination.iterdir()))
        finally:
            junction.rmdir()  # Exact junction only, never recursive deletion.

    def test_fsync_failure_before_link_does_not_publish_partial_record(self):
        with patch("ibkr_etl.owner_codex_trial_journal_local.os.fsync", side_effect=OSError()):
            with self.assertRaises(LocalJournalError):
                self.backend.create_if_absent(key_for_slot(1), b"synthetic")
        self.assertIsNone(self.backend.read(key_for_slot(1)))
        self.assertEqual([], list(self.root.iterdir()))

    def test_lost_link_response_retains_complete_record_not_rewritten(self):
        original = os.link
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("synthetic lost response after publication")
        with patch("ibkr_etl.owner_codex_trial_journal_local.os.link", side_effect=lost):
            with self.assertRaises(LocalJournalError):
                self.backend.create_if_absent(key_for_slot(1), b"synthetic")
        self.assertEqual(b"synthetic", self.backend.read(key_for_slot(1)))
        self.assertFalse(self.backend.create_if_absent(key_for_slot(1), b"replacement"))
        self.assertEqual(b"synthetic", self.backend.read(key_for_slot(1)))

    def test_unsupported_atomic_link_never_falls_back_to_copy(self):
        with patch("ibkr_etl.owner_codex_trial_journal_local.os.link", side_effect=OSError()):
            with self.assertRaises(LocalJournalError):
                self.backend.create_if_absent(key_for_slot(1), b"synthetic")
        self.assertIsNone(self.backend.read(key_for_slot(1)))
        self.assertEqual([], list(self.root.iterdir()))

    def test_live_mode_is_refused_by_journal(self):
        value = replace(synthetic_plan(self.operation), mode=TrialMode.LIVE_DISPOSABLE)
        journal = DurableTrialJournal(self.backend)
        with self.assertRaises(LifecycleError) as caught:
            journal.reserve_phase(value, Phase.SETUP, (), None)
        self.assertEqual("context_unverified", caught.exception.code)
        self.assertEqual([], list(self.root.iterdir()))


if __name__ == "__main__":
    unittest.main()
