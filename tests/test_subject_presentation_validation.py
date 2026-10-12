from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import generate_month_end_package as monthly
import render_onboarding_historical_reports as historical
import validate_subject_presentation as gate
from ibkr_etl.presentation_design import (
    ALPHA_WORLD_MS_PRESENTATION_CONTENT_CONTRACT_VERSION as MS_VERSION,
    PLATFORM_PRESENTATION_CONTENT_CONTRACT_VERSION as GENERIC_VERSION,
    presentation_content_contract_version,
    presentation_content_requirements,
)


class SubjectPresentationGateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.output = self.root / 'deck.pptx'
        self.output.write_bytes(b'private test deck')
        self.snapshot = self.root / 'snapshot.json'
        self.payload = {'subject_id': 'alpha-world-ms', 'period_end': '2026-09-30',
                        'reporting_currency': 'HKD'}
        self.write_snapshot()
        self.workbook_mock = patch.object(gate, 'verify_ms_chart_workbooks', side_effect=lambda _, **kwargs:
            {'status': 'passed', 'embedded_workbooks_verified': 6, 'owner_slides': [6,7,8,9,10,11],
             'output_sha256': hashlib.sha256(self.output.read_bytes()).hexdigest()})
        self.workbook_mock.start()
        self.addCleanup(self.workbook_mock.stop)

    def write_snapshot(self):
        self.snapshot.write_text(json.dumps(self.payload), encoding='utf-8')

    def financial(self):
        return {'status': 'passed', 'financial_charts_verified': 6, 'risk_metrics_verified': 7,
                'snapshot_sha256': hashlib.sha256(self.snapshot.read_bytes()).hexdigest(),
                'generic_validation': {'slides': 20, 'performance_axis': {}},
                'version': 'alpha-world-ms-presentation-hkd-hibor-v1'}

    def run_gate(self, subject_id='alpha-world-ms', strategy='iStrategy: Alpha World MS'):
        return gate.validate_subject_presentation(self.output, self.snapshot,
            subject_id=subject_id, period_end='2026-09-30', expected_strategy=strategy)

    def test_subject_contracts_keep_ms_t_and_generic_distinct(self):
        self.assertNotEqual(MS_VERSION, GENERIC_VERSION)
        self.assertEqual(presentation_content_contract_version('alpha-world-ms'), MS_VERSION)
        for subject in ('U16088571', 'U16088606', 'U99999999'):
            with self.subTest(subject=subject):
                self.assertEqual(presentation_content_contract_version(subject), GENERIC_VERSION)
                self.assertEqual(len(presentation_content_requirements(subject)), 5)
        self.assertGreater(len(presentation_content_requirements('alpha-world-ms')), 5)
        self.assertEqual(presentation_content_contract_version('alpha-world-t'),
                         'alpha-world-t-v24-hkd-hibor-internal-crisis-v1')
        self.assertNotEqual(presentation_content_contract_version('alpha-world-t'), MS_VERSION)
        self.assertEqual(len(presentation_content_requirements('alpha-world-t')), 6)

    def test_both_workers_share_exact_output_gate(self):
        self.assertIs(monthly.validate_subject_presentation, gate.validate_subject_presentation)
        self.assertIs(historical.validate_subject_presentation, gate.validate_subject_presentation)

    def test_ms_receipt_binds_financial_audit_snapshot_output_and_contract(self):
        before = self.snapshot.read_bytes()
        with patch.object(gate, 'audit', return_value=self.financial()) as audit:
            result = self.run_gate()
        audit.assert_called_once_with(self.output, self.snapshot)
        self.assertEqual(result['content_contract_version'], MS_VERSION)
        self.assertEqual(result['output_sha256'], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual(result['snapshot_sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(result['ms_financial_audit']['risk_metrics_verified'], 7)
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_wrong_subject_period_currency_and_identity_fail_before_audit(self):
        for field, value, error in (
            ('subject_id', 'alpha-world-t', 'subject or period'),
            ('period_end', '2026-08-31', 'subject or period'),
            ('reporting_currency', 'USD', 'Legacy USD'),
        ):
            old = self.payload[field]
            self.payload[field] = value
            self.write_snapshot()
            with self.subTest(field=field), patch.object(gate, 'audit') as audit:
                with self.assertRaisesRegex(ValueError, error):
                    self.run_gate()
                audit.assert_not_called()
            self.payload[field] = old
        self.write_snapshot()
        with patch.object(gate, 'audit') as audit:
            with self.assertRaisesRegex(ValueError, 'approved Alpha World MS identity'):
                self.run_gate(strategy='iStrategy: Another Account')
            audit.assert_not_called()

    def test_incomplete_or_stale_audit_is_not_registered_as_passed(self):
        for field, value in (('status', 'failed'), ('financial_charts_verified', 5),
                             ('risk_metrics_verified', 6), ('snapshot_sha256', 'wrong')):
            result = self.financial()
            result[field] = value
            with self.subTest(field=field), patch.object(gate, 'audit', return_value=result):
                with self.assertRaisesRegex(ValueError, 'incomplete or bound'):
                    self.run_gate()

    def test_audit_failure_propagates(self):
        with patch.object(gate, 'audit', side_effect=ValueError('MS heading differs')):
            with self.assertRaisesRegex(ValueError, 'heading differs'):
                self.run_gate()

    def test_missing_chart_workbooks_block_shared_gate(self):
        with (patch.object(gate, 'audit', return_value=self.financial()),
              patch.object(gate, 'verify_ms_chart_workbooks', side_effect=ValueError('missing embedded workbook'))):
            with self.assertRaisesRegex(ValueError, 'missing embedded workbook'):
                self.run_gate()

    def test_ms_shared_gate_requires_explicit_number_format_qualification(self):
        with (patch.object(gate, 'audit', return_value=self.financial()),
              patch.object(gate, 'verify_ms_chart_workbooks', side_effect=ValueError('number styles missing')) as workbook):
            with self.assertRaisesRegex(ValueError, 'number styles missing'):
                self.run_gate()
        workbook.assert_called_once_with(self.output, require_format=True)

    def test_incomplete_or_wrong_output_workbook_receipt_blocks_shared_gate(self):
        for field, value in (('status', 'failed'), ('embedded_workbooks_verified', 5),
                             ('owner_slides', [6,7,8,9,10]), ('output_sha256', 'wrong')):
            result = {'status': 'passed', 'embedded_workbooks_verified': 6,
                      'owner_slides': [6,7,8,9,10,11],
                      'output_sha256': hashlib.sha256(self.output.read_bytes()).hexdigest()}
            result[field] = value
            with (self.subTest(field=field), patch.object(gate, 'audit', return_value=self.financial()),
                  patch.object(gate, 'verify_ms_chart_workbooks', return_value=result)):
                with self.assertRaisesRegex(ValueError, 'workbook acceptance is incomplete'):
                    self.run_gate()

    def test_mutation_during_validation_fails(self):
        for target in (self.output, self.snapshot):
            self.output.write_bytes(b'private test deck')
            self.write_snapshot()
            financial = self.financial()
            def mutate(*_args):
                target.write_bytes(b'changed during validation')
                return financial
            with self.subTest(target=target.name), patch.object(gate, 'audit', side_effect=mutate):
                with self.assertRaisesRegex(ValueError, 'changed during validation'):
                    self.run_gate()

    def test_generic_account_uses_existing_validator_not_ms_audit(self):
        self.payload['subject_id'] = 'U99999999'
        self.payload['reporting_currency'] = 'USD'
        self.write_snapshot()
        expectations = {'security_axis_labels': {}, 'summary_table_rows': {}, 'slide_text': {}}
        with (patch.object(gate, 'platform_content_expectations', return_value=expectations),
              patch.object(gate, 'validate', return_value={'slides': 20}) as validate,
              patch.object(gate, 'audit') as audit):
            result = self.run_gate('U99999999', 'iStrategy: Future Account')
        audit.assert_not_called()
        self.assertEqual(result['content_contract_version'], GENERIC_VERSION)
        self.assertNotIn('ms_hkd', validate.call_args.kwargs)
        self.assertNotIn('ms_financial_audit', result)

    def test_historical_render_returns_shared_gate_receipt(self):
        financial = self.financial()
        with (patch.object(historical.subprocess, 'run') as render,
              patch.object(gate, 'audit', return_value=financial)):
            render.return_value.returncode = 0
            result = historical._render_presentation(template=self.root / 'template.pptx',
                snapshot=self.snapshot, output=self.output, period_end='2026-09-30',
                subject_id='alpha-world-ms', expected_strategy='iStrategy: Alpha World MS')
        self.assertEqual(result['content_contract_version'], MS_VERSION)
        self.assertEqual(result['ms_financial_audit']['financial_charts_verified'], 6)
