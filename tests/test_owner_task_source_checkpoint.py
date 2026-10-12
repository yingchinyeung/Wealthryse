"""Pure historical-linkage tests, NOT live PostgreSQL or protected readback QA."""
import copy
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import verify_owner_task_source_checkpoint as checkpoint


EXECUTION = 'ibkrv2stg-bootstrap-85v8wm8'


def expected_cleanup_record():
    # Independent eight-field oracle, not the implementation's expected().
    return dict(status='passed', stage='failed-setup-cleanup',
        trial_database_removed=True, trial_role_removed=True,
        production_database_touched=False, ibkr_source_writes=False,
        existing_service_grants_changed=False, model_calls=0)


def history():
    source = dict(files={'src/ibkr_etl/owner_codex_tasks.py': 'a'*64,
                         'scripts/run_owner_task_postgres_trial.py': 'b'*64},
                  payload_sha256='c'*64)
    original_raw = dict(protected_receipt={'sha256': 'd'*64, 'version': 'synthetic-intent-version'})
    original = dict(stage='run', startup_retry=True, source=copy.deepcopy(source),
        baseline=dict(job_inventory_complete=True,
                      other_jobs={f'synthetic-job-{n}': 'e'*64 for n in range(34)}))
    dispatch = dict(stage='run', execution=EXECUTION, source=copy.deepcopy(source),
                    intent_receipt=copy.deepcopy(original_raw['protected_receipt']))
    metadata_raw = dict(protected_receipt={'sha256': 'f'*64, 'version': 'synthetic-metadata-version'})
    metadata = dict(status='observed', failed_trial=EXECUTION, baseline_unchanged=True,
                    source=copy.deepcopy(source))
    cleanup = dict(status='passed', record=expected_cleanup_record(), baseline_unchanged=True,
        metadata_receipt=copy.deepcopy(metadata_raw['protected_receipt']), source=copy.deepcopy(source))
    return [original_raw, original, dispatch, metadata_raw, metadata, cleanup]


class OwnerTaskSourceCheckpointTests(unittest.TestCase):
    def reject(self, values):
        with self.assertRaises(ValueError):
            checkpoint.validate_history(*values)

    def test_valid_exact_history_returns_known_job_count_without_side_effects(self):
        values = history()
        original = copy.deepcopy(values)
        with (mock.patch.object(checkpoint.deploy, 'protected', side_effect=AssertionError('no cloud read')),
             mock.patch.object(checkpoint.deploy, 'request', side_effect=AssertionError('no HTTP call')),
             mock.patch.object(checkpoint.deploy, 'az', side_effect=AssertionError('no CLI call'))):
            self.assertEqual(checkpoint.validate_history(*values), 34)
        self.assertEqual(values, original)

    def test_original_stage_and_reviewed_startup_retry_are_required(self):
        for field, value in (('stage', 'cleanup'), ('stage', None),
                             ('startup_retry', False), ('startup_retry', 1), ('startup_retry', None)):
            with self.subTest(field=field, value=value):
                values = history()
                values[1][field] = value
                self.reject(values)
        values = history()
        del values[1]['startup_retry']
        self.reject(values)

    def test_job_inventory_must_be_explicitly_complete_not_unknown_or_boolean_alias(self):
        for value in (False, None, 0, 1, 'true'):
            with self.subTest(value=value):
                values = history()
                values[1]['baseline']['job_inventory_complete'] = value
                self.reject(values)
        values = history()
        del values[1]['baseline']['job_inventory_complete']
        self.reject(values)

    def test_inventory_has_exactly_34_known_other_jobs(self):
        for count in (0, 33, 35):
            with self.subTest(count=count):
                values = history()
                values[1]['baseline']['other_jobs'] = {f'synthetic-job-{n}': 'e'*64 for n in range(count)}
                self.reject(values)
        for value in (None, 'x'*34):
            with self.subTest(value=value):
                values = history()
                values[1]['baseline']['other_jobs'] = value
                self.reject(values)
        values = history()
        del values[1]['baseline']['other_jobs']
        self.reject(values)

    def test_dispatch_is_bound_to_exact_run_stage_and_failed_execution(self):
        for field, value in (('stage', 'cleanup'), ('stage', None),
                             ('execution', 'ibkrv2stg-bootstrap-different'), ('execution', None)):
            with self.subTest(field=field, value=value):
                values = history()
                values[2][field] = value
                self.reject(values)

    def test_dispatch_is_bound_to_entire_original_source_not_only_file_hashes(self):
        for mutation in ('files', 'payload', 'extra'):
            with self.subTest(mutation=mutation):
                values = history()
                if mutation == 'files':
                    values[2]['source']['files']['src/ibkr_etl/owner_codex_tasks.py'] = '0'*64
                elif mutation == 'payload':
                    values[2]['source']['payload_sha256'] = '0'*64
                else:
                    values[2]['source']['unreviewed_field'] = True
                self.reject(values)

    def test_dispatch_receipt_matches_exact_original_protected_receipt(self):
        for target in (0, 2):
            with self.subTest(target=target):
                values = history()
                key = 'protected_receipt' if target == 0 else 'intent_receipt'
                values[target][key]['version'] = 'different-intent-version'
                self.reject(values)

    def test_metadata_is_observed_not_a_fabricated_sql_success(self):
        for value in ('passed', 'failed', None):
            with self.subTest(value=value):
                values = history()
                values[4]['status'] = value
                self.reject(values)

    def test_metadata_trial_execution_binding_is_exact(self):
        values = history()
        values[4]['failed_trial'] = 'ibkrv2stg-bootstrap-different'
        self.reject(values)

    def test_metadata_and_cleanup_require_strict_baseline_unchanged_boolean(self):
        for target in (4, 5):
            for value in (False, None, 1, 'true'):
                with self.subTest(target=target, value=value):
                    values = history()
                    values[target]['baseline_unchanged'] = value
                    self.reject(values)

    def test_metadata_and_cleanup_file_sets_match_original_exactly(self):
        for target in (4, 5):
            for mutation in ('different-hash', 'missing-file', 'additional-file'):
                with self.subTest(target=target, mutation=mutation):
                    values = history()
                    files = values[target]['source']['files']
                    if mutation == 'different-hash':
                        files['src/ibkr_etl/owner_codex_tasks.py'] = '0'*64
                    elif mutation == 'missing-file':
                        del files['src/ibkr_etl/owner_codex_tasks.py']
                    else:
                        files['unreviewed.py'] = '0'*64
                    self.reject(values)

    def test_cleanup_receipt_is_bound_to_exact_metadata_receipt(self):
        for target in (3, 5):
            with self.subTest(target=target):
                values = history()
                key = 'protected_receipt' if target == 3 else 'metadata_receipt'
                values[target][key]['sha256'] = '0'*64
                self.reject(values)

    def test_cleanup_envelope_itself_must_have_passed(self):
        for value in ('observed', 'failed', None, True):
            with self.subTest(value=value):
                values = history()
                values[5]['status'] = value
                self.reject(values)

    def test_cleanup_record_has_exact_eight_fields(self):
        self.assertEqual(len(expected_cleanup_record()), 8)
        for field in expected_cleanup_record():
            with self.subTest(missing=field):
                values = history()
                del values[5]['record'][field]
                self.reject(values)
        values = history()
        values[5]['record']['live_sql_tests_passed'] = True
        self.reject(values)

    def test_cleanup_record_status_and_stage_cannot_be_substituted(self):
        for field, value in (('status', 'observed'), ('status', 'failed'),
                             ('stage', 'cleanup'), ('stage', 'run')):
            with self.subTest(field=field, value=value):
                values = history()
                values[5]['record'][field] = value
                self.reject(values)

    def test_removed_flags_require_true_not_integer_one(self):
        for field in ('trial_database_removed', 'trial_role_removed'):
            for value in (False, 1, None, 'true'):
                with self.subTest(field=field, value=value):
                    values = history()
                    values[5]['record'][field] = value
                    self.reject(values)

    def test_source_write_touch_and_grant_flags_require_false_not_integer_zero(self):
        for field in ('production_database_touched', 'ibkr_source_writes', 'existing_service_grants_changed'):
            for value in (True, 0, None, 'false'):
                with self.subTest(field=field, value=value):
                    values = history()
                    values[5]['record'][field] = value
                    self.reject(values)

    def test_model_calls_requires_exact_integer_zero_not_false_or_float(self):
        for value in (False, True, 0.0, 1, -1, None, '0'):
            with self.subTest(value=value):
                values = history()
                values[5]['record']['model_calls'] = value
                self.reject(values)


if __name__ == '__main__':
    unittest.main()
