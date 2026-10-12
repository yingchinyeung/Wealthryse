import hashlib
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from azure.core.exceptions import ResourceExistsError
import render_onboarding_historical_reports as renderer
import run_self_service_onboarding_reports as worker
from ibkr_etl.monthly_reports import validate_review_artifact


class ImmutableReviewTests(unittest.TestCase):
    def test_new_content_gets_distinct_in_scope_references(self):
        hashes = [hashlib.sha256(x).hexdigest() for x in (b'old', b'new')]
        refs = [renderer._immutable_review_blob('alpha-world-ms','2026-09-30',Path('alpha-world-ms_Factsheet_2026-09-30.pdf'),h) for h in hashes]
        self.assertNotEqual(*refs)
        for ref,digest in zip(refs,hashes):
            validate_review_artifact({'generation_run_id':1,'report_type':'factsheet',
                'output_reference':'internal-reports/'+ref,'output_sha256':digest},
                account_id='alpha-world-ms',period_end='2026-09-30',container='internal-reports')

    def test_write_is_create_only_and_read_back(self):
        client=Mock();client.download_blob.return_value.readall.return_value=b'new'
        renderer._upload_review(client,b'new',metadata={},content_type='application/pdf')
        self.assertFalse(client.upload_blob.call_args.kwargs['overwrite'])
        client.download_blob.assert_called_once()

    def test_identical_existing_content_is_idempotent(self):
        client=Mock();client.upload_blob.side_effect=ResourceExistsError('exists')
        client.download_blob.return_value.readall.return_value=b'new'
        renderer._upload_review(client,b'new',metadata={},content_type='application/pdf')

    def test_conflicting_existing_content_fails_closed(self):
        client=Mock();client.upload_blob.side_effect=ResourceExistsError('exists')
        client.download_blob.return_value.readall.return_value=b'other'
        with self.assertRaisesRegex(RuntimeError,'differs'):
            renderer._upload_review(client,b'new',metadata={},content_type='application/pdf')

    def test_failed_readback_fails_closed(self):
        client=Mock();client.download_blob.return_value.readall.return_value=b'other'
        with self.assertRaisesRegex(RuntimeError,'integrity'):
            renderer._upload_review(client,b'new',metadata={},content_type='application/pdf')


class ExistingMsSnapshotTests(unittest.TestCase):
    def test_wrong_currency_rejected(self):
        with self.assertRaisesRegex(ValueError,'HKD contract'):
            worker._existing_ms_snapshots(Mock(),{'subject_id':'alpha-world-ms','reporting_currency':'USD'},'tenant')

    def test_ms_reuses_validated_snapshot_without_refreshing_upstream(self):
        c=Mock();c.execute.side_effect=[Mock(fetchall=Mock(return_value=[{'period_end':'2026-09-30'}])),
            Mock(fetchone=Mock(return_value={'quality_status':'warn','publication_allowed':False,'reporting_currency':'HKD'}))]
        batch={'subject_id':'alpha-world-ms','reporting_currency':'HKD','batch_id':'batch',
            'reporting_start':'2025-01-20','reporting_end':'2026-09-30'}
        payload={'reporting_currency':'HKD','period_start':'2025-01-20','period_end':'2026-09-30',
            'data_as_of':'2026-09-30','risk':{'metrics':{'return':1}}}
        with (patch('ibkr_etl.subject_read_contract.require_ms_hkd_contract') as contract,
                patch('ibkr_etl.reporting_snapshot.active_month_end_snapshot_id',return_value='snapshot'),
                patch('generate_month_end_package._load_frozen_snapshot',return_value=(payload,'digest')),
                patch('ibkr_etl.alpha_world_ms_analytics.analyze',return_value=({'return':1},(),{})),
                patch('ibkr_etl.alpha_world_ms_hkd.load_risk_snapshot',return_value=(None,{'return':1},None)),
                patch.object(worker,'refresh_composite_crisis_replay') as crisis):
            result=worker._existing_ms_snapshots(c,batch,'tenant')
        contract.assert_called_once_with(c,tenant_id='tenant');crisis.assert_not_called()
        self.assertEqual(result['items'][0]['snapshot_id'],'snapshot')
        self.assertFalse(result['publication_approval_changed'])
        for call in c.execute.call_args_list:self.assertTrue(call.args[0].lstrip().startswith('SELECT'))


if __name__=='__main__':unittest.main()
