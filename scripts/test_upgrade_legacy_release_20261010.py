"""Offline negative/positive guard tests; no Azure calls."""
import copy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch
from control_upgrade_legacy_ingestion_20261010 import accept, CHECKS
from deploy_upgrade_legacy_security_20261010 import validate_rehearsal, window


class ReleaseTests(unittest.TestCase):
    def proof(self):
        return {'passed':True,'checks':sorted(CHECKS),'public_tables_checked':25,'public_table_differences':[],
                'temporary_relations':25,'database_read_only_enforced':True,'temporary_objects_dropped_on_disconnect':True,
                **{k:0 for k in ('production_connections','production_writes','persistent_clone_writes','broker_calls','cloud_storage_writes')}}

    def test_exact_proof_accepted(self): accept(self.proof())

    def test_each_external_side_effect_rejected(self):
        for key in ('production_connections','production_writes','persistent_clone_writes','broker_calls','cloud_storage_writes'):
            p=self.proof();p[key]=1
            with self.subTest(key=key),self.assertRaises(ValueError): accept(p)

    def test_missing_or_duplicate_check_rejected(self):
        for checks in (sorted(CHECKS)[:-1],sorted(CHECKS)+[sorted(CHECKS)[0]]):
            p=self.proof();p['checks']=checks
            with self.assertRaises(ValueError): accept(p)

    def test_database_guard_and_preservation_required(self):
        for key,value in (('database_read_only_enforced',False),('temporary_objects_dropped_on_disconnect',False),('public_tables_checked',24),('public_table_differences',['nav_snapshots']),('temporary_relations',24)):
            p=self.proof();p[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): accept(p)

    def test_image_and_saved_job_must_match(self):
        p={'passed':True,'image':'digest','saved_job_preserved':True,'proof':self.proof()}
        validate_rehearsal(p,'digest')
        with self.assertRaises(ValueError): validate_rehearsal(p,'other')
        p['saved_job_preserved']=False
        with self.assertRaises(ValueError): validate_rehearsal(p,'digest')

    @patch('deploy_upgrade_legacy_security_20261010.runtime.safe_window')
    def test_scope_and_schedule_guard(self, safe):
        for job in ('ibkr-v11-daily','ibkr-v11-bootstrap','ibkr-v11-history-backfill'):window(job)
        self.assertEqual(safe.call_count,3)
        with self.assertRaises(ValueError):window('unrelated-job')
        safe.side_effect=ValueError('Scheduled window')
        with self.assertRaises(ValueError):window('ibkr-v11-daily')


if __name__=='__main__': unittest.main()
