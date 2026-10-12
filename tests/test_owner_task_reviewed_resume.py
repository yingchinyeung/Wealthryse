"""Source-only reviewed-resume guards; synthetic records, no SQL/Azure calls."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import resume_owner_task_postgres_trial as resume


# These are independent reviewed scope/claim oracles, not imported production
# validator constants. Test records contain no real credentials or account data.
CHECKPOINT_SHA = "565c1fac22a131203b3cd856a91e462a7821e2dc0cd9b24cd74304a6de0713f5"
CHECKPOINT_VERSION = "2026-10-03T09:12:50.2999170Z"
MIGRATION_SHA = "b4a78f64969d328f6b3ce1ce71b73cd3c033606fc8580ed1a933dd47857e9861"
DEPENDENCIES = (
    "src/ibkr_etl/util.py", "src/ibkr_etl/db.py", "src/ibkr_etl/owner_codex_tasks.py",
    "migrations/postgres/059_owner_codex_tasks.sql", "migrations/postgres/001_init.sql",
    "scripts/verify_owner_task_postgres.py", "scripts/run_owner_task_postgres_trial.py",
    "scripts/deploy_owner_task_postgres_trial.py", "scripts/owner_task_postgres_retry_guard.py",
    "scripts/run_specialist_gateway_read_verification.py", "src/ibkr_etl/specialist_archive_reader.py",
    "src/ibkr_etl/specialist_audit_service.py", "scripts/deploy_specialist_daily_schedule.py",
    "scripts/run_specialist_daily_publication.py", "scripts/deploy_metadata_monitor.py",
    "scripts/audit_azure_runtime.py", "scripts/release_verified_image.py",
    "scripts/deploy_specialist_report_worker.py",
)
CHECKS = [
    "exact_scope_role_catalog_and_migration",
    "forced_tenant_owner_and_missing_scope_rls",
    "immutability_null_checks_and_deferred_event_atomicity",
    "trigger_schema_binding_with_temp_decoys",
    "concurrent_idempotency_payload_conflict_and_single_event",
    "cancel_first_cas_race_sticky_expiry_and_verified_stop_gate",
    "late_cancel_no_redispatch_and_fresh_authority_revocation",
    "committed_final_catalog_readback",
]
RUN_FALSE = (
    "fresh_login_verified", "non_owner_privilege_boundary_verified", "protected_archive_live_verified",
    "production_migration_installed", "production_database_touched", "ibkr_source_writes",
    "existing_service_grants_changed", "owner_runtime_active", "cleanup_performed",
)
RUN_TRUE = ("receipt_verifier_is_synthetic", "trial_retained_for_review")
RUN_COUNTS = ("source_rows_read", "source_rows_written", "service_grants_created", "model_calls", "agents_activated")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def source_record():
    files = {name: hashlib.sha256(("SYNTHETIC:" + name).encode()).hexdigest() for name in DEPENDENCIES}
    files["migrations/postgres/059_owner_codex_tasks.sql"] = MIGRATION_SHA
    return {"files": files, "payload_sha256": "a" * 64, "checkpoint_validator_sha256": "e" * 64}


def checkpoint_record(source=None):
    source = source or source_record()
    return {
        "status": "validated-source-live-sql-pending", "synthetic_scope_removed": True,
        "baseline_unchanged": True, "source_only_wrapper_fix": True,
        "production_migration_059_installed": False, "owner_runtime_active": False,
        "six_specialist_runtimes_active": False, "ibkr_source_writes": False,
        "existing_service_grants_changed": False, "model_calls": 0,
        "existing_jobs_unchanged": 34, "daily_schedule_hkt": "18:00",
        "natural_daily_cycle_verified": False,
        "local_checks": {"test_count": 1438, "live_sql_tests_passed": False,
            "compilation_exit_code": 0, "unittest_exit_code": 0,
            "financial_or_visual_acceptance": False, "os_or_dependency_security_certification": False,
            "repository_policy": "PASS: SYNTHETIC local policy fixture; not a live certification"},
        "sources": dict(source["files"], **{"docs/SYNTHETIC_UNRELATED.md": "d" * 64,
            "scripts/verify_owner_task_source_checkpoint.py": source["checkpoint_validator_sha256"]}),
    }


def run_record():
    result = {
        "status": "passed", "stage": "run", "database": "owner_codex_acceptance_20261003",
        "trial_database": "owner_codex_acceptance_20261003", "schema": "owner_codex_059_trial",
        "effective_role": "ibkr_owner_codex_trial_20261003", "trial_role": "ibkr_owner_codex_trial_20261003",
        "scope": "synthetic_admin_context_effective_role_postgres_behavior", "server_version": "16.10",
        "migration_sha256": MIGRATION_SHA, "checks": list(CHECKS), "check_manifest_sha256": digest(CHECKS),
    }
    result.update({k: False for k in RUN_FALSE})
    result.update({k: True for k in RUN_TRUE})
    result.update({k: 0 for k in RUN_COUNTS})
    return result


def cleanup_record():
    return {"status": "passed", "stage": "cleanup", "trial_database_removed": True, "trial_role_removed": True,
            "production_database_touched": False, "ibkr_source_writes": False, "existing_service_grants_changed": False}


def prerequisite_records():
    checkpoint = checkpoint_record()
    cleanup_receipt = {"sha256": "c" * 64, "version_id": "2026-10-03T08:47:42.4073163Z", "synthetic": True}
    intent_receipt = {"sha256": "b" * 64, "version_id": "2026-10-03T08:21:10.4415794Z", "synthetic": True}
    checkpoint.update(cleanup_receipt=cleanup_receipt, trial_intent_receipt=intent_receipt,
        protected_receipt={"sha256": CHECKPOINT_SHA, "version_id": CHECKPOINT_VERSION, "synthetic": True})
    cleanup = {"status": "passed", "baseline_unchanged": True, "record": {
        "status": "passed", "stage": "failed-setup-cleanup", "trial_database_removed": True,
        "trial_role_removed": True, "production_database_touched": False, "ibkr_source_writes": False,
        "existing_service_grants_changed": False, "model_calls": 0}, "protected_receipt": cleanup_receipt}
    prior_intent = {"protected_receipt": intent_receipt, "baseline": {"synthetic": "prior unchanged baseline"},
                    "bootstrap_settings_sha256": "f" * 64}
    return checkpoint, cleanup, prior_intent


class ReviewedResumeTests(unittest.TestCase):
    def deny(self, function, *args):
        with self.assertRaises(ValueError):
            function(*args)

    def test_fixed_new_attempt_and_prior_checkpoint_oracles(self):
        self.assertEqual(resume.FOLDER.name, "owner-task-postgres-20261003-r2")
        self.assertEqual(resume.PRIOR_FOLDER.name, "owner-task-postgres-20261003-retry1")
        self.assertEqual(resume.CHECKPOINT_SHA, CHECKPOINT_SHA)
        self.assertEqual(resume.CHECKPOINT_VERSION, CHECKPOINT_VERSION)
        self.assertEqual(len(DEPENDENCIES), 18)

    def test_valid_source_only_checkpoint(self):
        source = source_record()
        self.assertIsNone(resume.validate_checkpoint(checkpoint_record(source), source))

    def test_checkpoint_status_does_not_claim_live_acceptance(self):
        for value in (None, "passed", "validated-source-live-sql-completed", True):
            record = checkpoint_record()
            record["status"] = value
            self.deny(resume.validate_checkpoint, record, source_record())

    def test_checkpoint_true_flags_require_boolean_true(self):
        for field in ("synthetic_scope_removed", "baseline_unchanged", "source_only_wrapper_fix"):
            for value in (False, 1, "true", None):
                record = checkpoint_record()
                record[field] = value
                with self.subTest(field=field, value=value):
                    self.deny(resume.validate_checkpoint, record, source_record())

    def test_checkpoint_false_flags_require_boolean_false(self):
        fields = ("production_migration_059_installed", "owner_runtime_active", "six_specialist_runtimes_active",
                  "ibkr_source_writes", "existing_service_grants_changed", "natural_daily_cycle_verified")
        for field in fields:
            for value in (True, 0, "false", None):
                record = checkpoint_record()
                record[field] = value
                with self.subTest(field=field, value=value):
                    self.deny(resume.validate_checkpoint, record, source_record())

    def test_checkpoint_model_and_inventory_counts_are_exact_integers(self):
        for field, wrong in (("model_calls", (True, False, "0", -1, 1, 0.0, None)),
                             ("existing_jobs_unchanged", (True, "34", 33, 35, 34.0, None))):
            for value in wrong:
                record = checkpoint_record()
                record[field] = value
                with self.subTest(field=field, value=value):
                    self.deny(resume.validate_checkpoint, record, source_record())

    def test_checkpoint_test_count_is_exact_historical_1438_not_new_test_count(self):
        for value in (True, "1438", 1437, 1439, 1438.0, None):
            record = checkpoint_record()
            record["local_checks"]["test_count"] = value
            self.deny(resume.validate_checkpoint, record, source_record())

    def test_checkpoint_local_claims_remain_source_only(self):
        for field in ("live_sql_tests_passed", "financial_or_visual_acceptance", "os_or_dependency_security_certification"):
            for value in (True, 0, "false", None):
                record = checkpoint_record()
                record["local_checks"][field] = value
                with self.subTest(field=field, value=value):
                    self.deny(resume.validate_checkpoint, record, source_record())

    def test_checkpoint_compile_and_unit_exit_codes_are_integer_zero(self):
        for field in ("compilation_exit_code", "unittest_exit_code"):
            for value in (True, False, 1, -1, "0", 0.0, None):
                record = checkpoint_record()
                record["local_checks"][field] = value
                with self.subTest(field=field, value=value):
                    self.deny(resume.validate_checkpoint, record, source_record())

    def test_checkpoint_repository_policy_requires_fixed_pass_prefix(self):
        for value in (None, True, "PASS", "PASS:", "FAIL: synthetic", " PASS: synthetic"):
            record = checkpoint_record()
            record["local_checks"]["repository_policy"] = value
            self.deny(resume.validate_checkpoint, record, source_record())

    def test_checkpoint_validator_code_hash_bound_to_prior_review(self):
        source = source_record()
        source["checkpoint_validator_sha256"] = "f" * 64
        self.deny(resume.validate_checkpoint, checkpoint_record(), source)
        record = checkpoint_record()
        del record["sources"]["scripts/verify_owner_task_source_checkpoint.py"]
        self.deny(resume.validate_checkpoint, record, source_record())

    def test_checkpoint_schedule_stays_1800_hkt(self):
        for value in ("09:00", "18:00 UTC", 18, None):
            record = checkpoint_record()
            record["daily_schedule_hkt"] = value
            self.deny(resume.validate_checkpoint, record, source_record())

    def test_source_dependency_hash_must_match_checkpoint(self):
        for name in DEPENDENCIES:
            record = checkpoint_record()
            record["sources"][name] = "f" * 64
            with self.subTest(name=name):
                self.deny(resume.validate_checkpoint, record, source_record())

    def test_source_requires_all_and_only_exact_18_dependencies(self):
        for files in ({}, {k: v for k, v in source_record()["files"].items() if k != DEPENDENCIES[0]},
                      dict(source_record()["files"], **{"scripts/unregistered.py": "b" * 64})):
            source = dict(source_record(), files=files)
            checkpoint = checkpoint_record()
            checkpoint["sources"].update(files)
            with self.subTest(keys=tuple(files)):
                self.deny(resume.validate_checkpoint, checkpoint, source)

    def test_equal_but_malformed_dependency_digests_do_not_validate(self):
        for value in (None, True, "", "a" * 63, "A" * 64, "not-sha256"):
            source = source_record()
            source["files"][DEPENDENCIES[0]] = value
            record = checkpoint_record(source)
            self.deny(resume.validate_checkpoint, record, source)

    def test_checkpoint_missing_dependency_denied(self):
        record = checkpoint_record()
        del record["sources"][DEPENDENCIES[0]]
        self.deny(resume.validate_checkpoint, record, source_record())

    def test_valid_synthetic_run_record(self):
        self.assertIsNone(resume.validate_run_record(run_record(), source_record()))

    def test_run_exact_database_role_schema_and_scope_required(self):
        for field in ("database", "trial_database", "schema", "effective_role", "trial_role", "scope", "status", "stage"):
            record = run_record()
            record[field] = "ibkr_staging" if "database" in field else "unregistered"
            with self.subTest(field=field):
                self.deny(resume.validate_run_record, record, source_record())

    def test_run_server_version_only_postgresql_16_family(self):
        for value in (None, True, 16.10, "15.10", "17.0", "16", "16.10\n", " 16.10"):
            record = run_record()
            record["server_version"] = value
            self.deny(resume.validate_run_record, record, source_record())
        record = run_record()
        record["server_version"] = "16.10 (SYNTHETIC test vendor)"
        self.assertIsNone(resume.validate_run_record(record, source_record()))

    def test_run_false_flags_cannot_claim_production_or_real_privilege_acceptance(self):
        for field in RUN_FALSE:
            for value in (True, 0, "false", None):
                record = run_record()
                record[field] = value
                with self.subTest(field=field, value=value):
                    self.deny(resume.validate_run_record, record, source_record())

    def test_run_synthetic_and_retention_true_flags_require_boolean(self):
        for field in RUN_TRUE:
            for value in (False, 1, "true", None):
                record = run_record()
                record[field] = value
                with self.subTest(field=field, value=value):
                    self.deny(resume.validate_run_record, record, source_record())

    def test_run_zero_effect_counts_reject_booleans_floats_and_nonzero(self):
        for field in RUN_COUNTS:
            for value in (True, False, "0", 0.0, -1, 1, None):
                record = run_record()
                record[field] = value
                with self.subTest(field=field, value=value):
                    self.deny(resume.validate_run_record, record, source_record())

    def test_run_check_list_is_exact_ordered_eight_not_pass_count(self):
        for checks in (CHECKS[:-1], list(reversed(CHECKS)), CHECKS + ["production_verified"], tuple(CHECKS), 8, None):
            record = run_record()
            record["checks"] = checks
            self.deny(resume.validate_run_record, record, source_record())

    def test_run_check_manifest_hash_and_migration_hash_required(self):
        for field in ("migration_sha256", "check_manifest_sha256"):
            for value in (None, True, "0" * 64, "", "A" * 64):
                record = run_record()
                record[field] = value
                with self.subTest(field=field, value=value):
                    self.deny(resume.validate_run_record, record, source_record())

    def test_run_missing_flags_and_counts_denied(self):
        for field in RUN_FALSE + RUN_TRUE + RUN_COUNTS:
            record = run_record()
            del record[field]
            self.deny(resume.validate_run_record, record, source_record())

    def test_valid_cleanup_is_exact_seven_field_record(self):
        record = cleanup_record()
        self.assertEqual(len(record), 7)
        self.assertIsNone(resume.validate_cleanup_record(record))

    def test_cleanup_rejects_added_or_missing_claims(self):
        record = cleanup_record()
        record["model_calls"] = 0
        self.deny(resume.validate_cleanup_record, record)
        for field in cleanup_record():
            record = cleanup_record()
            del record[field]
            self.deny(resume.validate_cleanup_record, record)

    def test_cleanup_strict_boolean_identity_and_exact_status_stage(self):
        for field, value in cleanup_record().items():
            values = (False, 1, "true", None) if value is True else (True, 0, "false", None) if value is False else ("failed", True, None)
            for change in values:
                record = cleanup_record()
                record[field] = change
                with self.subTest(field=field, value=change):
                    self.deny(resume.validate_cleanup_record, record)

    def test_valid_prior_checkpoint_and_cleanup_bindings(self):
        records = prerequisite_records()
        with patch.object(Path, "read_text", side_effect=[json.dumps(r) for r in records]), \
                patch.object(resume.deploy, "protected", side_effect=lambda value, kind: {k: v for k, v in value.items() if k != "protected_receipt"}) as protected:
            result = resume.prerequisites(source_record())
        self.assertEqual(result, (records[0]["protected_receipt"], records[1]["protected_receipt"],
                                  records[2]["baseline"], records[2]["bootstrap_settings_sha256"]))
        self.assertEqual([call.args[1] for call in protected.call_args_list], ["source-checkpoint", "failed-cleanup-accepted", "run-intent"])

    def test_wrong_pinned_checkpoint_hash_or_version_fails_before_archive_read(self):
        for field in ("sha256", "version_id"):
            checkpoint = prerequisite_records()[0]
            checkpoint["protected_receipt"][field] = "unregistered"
            with patch.object(Path, "read_text", return_value=json.dumps(checkpoint)), patch.object(resume.deploy, "protected") as protected:
                self.deny(resume.prerequisites, source_record())
            protected.assert_not_called()

    def test_cleanup_receipt_mismatch_blocks_new_attempt(self):
        checkpoint, cleanup, original = prerequisite_records()
        cleanup["protected_receipt"] = dict(cleanup["protected_receipt"], sha256="f" * 64)
        with patch.object(Path, "read_text", side_effect=[json.dumps(checkpoint), json.dumps(cleanup), json.dumps(original)]), \
                patch.object(resume.deploy, "protected", side_effect=lambda value, kind: {k: v for k, v in value.items() if k != "protected_receipt"}):
            self.deny(resume.prerequisites, source_record())

    def test_cleanup_prior_acceptance_strict_types_and_record_shape(self):
        for change in (lambda r: r.update(baseline_unchanged=1),
                       lambda r: r["record"].update(model_calls=False),
                       lambda r: r["record"].update(existing_service_grants_changed=True),
                       lambda r: r["record"].update(extra="unregistered")):
            checkpoint, cleanup, original = prerequisite_records()
            change(cleanup)
            with patch.object(Path, "read_text", side_effect=[json.dumps(checkpoint), json.dumps(cleanup), json.dumps(original)]), \
                    patch.object(resume.deploy, "protected", side_effect=lambda value, kind: {k: v for k, v in value.items() if k != "protected_receipt"}):
                self.deny(resume.prerequisites, source_record())

    def test_archive_verification_failure_blocks_prerequisites(self):
        checkpoint = prerequisite_records()[0]
        with patch.object(Path, "read_text", return_value=json.dumps(checkpoint)), \
                patch.object(resume.deploy, "protected", side_effect=ValueError("synthetic archive unavailable")):
            self.deny(resume.prerequisites, source_record())

    def test_prior_baseline_intent_receipt_cannot_be_substituted(self):
        checkpoint, cleanup, original = prerequisite_records()
        original["protected_receipt"] = dict(original["protected_receipt"], sha256="f" * 64)
        with patch.object(Path, "read_text", side_effect=[json.dumps(checkpoint), json.dumps(cleanup), json.dumps(original)]), \
                patch.object(resume.deploy, "protected", side_effect=lambda value, kind: {k: v for k, v in value.items() if k != "protected_receipt"}):
            self.deny(resume.prerequisites, source_record())

    def test_current_source_binds_new_dispatcher_validator_and_candidate_hash(self):
        wanted, source = {"synthetic": True}, source_record()
        dispatcher, validator, candidate = b"SYNTH_DISPATCHER", b"SYNTH_VALIDATOR", {"synthetic": "manifest"}
        with patch.object(resume.deploy, "template", return_value=(wanted, copy.deepcopy(source))) as template, \
                patch.object(Path, "read_bytes", side_effect=(dispatcher, validator)), \
                patch.object(resume.qa, "source_manifest", return_value=candidate):
            actual_wanted, actual = resume.current_source({"synthetic": "job"}, "run")
        template.assert_called_once_with({"synthetic": "job"}, "run")
        self.assertEqual(actual_wanted, wanted)
        self.assertEqual(actual["files"], source["files"])
        self.assertEqual(actual["resume_dispatcher_sha256"], hashlib.sha256(dispatcher).hexdigest())
        self.assertEqual(actual["checkpoint_validator_sha256"], hashlib.sha256(validator).hexdigest())
        self.assertEqual(actual["candidate_manifest_sha256"], digest(candidate))

    def test_record_validators_do_not_call_cloud_or_dispatch(self):
        with patch.object(resume.deploy, "request") as request, patch.object(resume.deploy, "az") as az:
            resume.validate_checkpoint(checkpoint_record(), source_record())
            resume.validate_run_record(run_record(), source_record())
            resume.validate_cleanup_record(cleanup_record())
        request.assert_not_called()
        az.assert_not_called()


if __name__ == "__main__":
    unittest.main()
