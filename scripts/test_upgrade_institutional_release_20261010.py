"""Offline negative/positive guard tests; no Azure calls."""
import copy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch
from control_upgrade_institutional_postgres_20261010 import accept, CHECKS
from deploy_upgrade_institutional_security_20261010 import validate_rehearsal, window


class ReleaseTests(unittest.TestCase):
    def proof(self):
        return {'passed':True,'checks':sorted(CHECKS),'public_tables_checked':92,'public_table_differences':[],
                'temporary_relations':7,'database_read_only_enforced':True,'temporary_objects_dropped_on_disconnect':True,
                **{k:0 for k in ('production_connections','production_writes','persistent_clone_writes','broker_calls','market_calls')}}

    def test_exact_proof_accepted(self): accept(self.proof())

    def test_each_external_side_effect_rejected(self):
        for key in ('production_connections','production_writes','persistent_clone_writes','broker_calls','market_calls'):
            p=self.proof();p[key]=1
            with self.subTest(key=key),self.assertRaises(ValueError): accept(p)

    def test_missing_or_duplicate_check_rejected(self):
        for checks in (sorted(CHECKS)[:-1],sorted(CHECKS)+[sorted(CHECKS)[0]]):
            p=self.proof();p['checks']=checks
            with self.assertRaises(ValueError): accept(p)

    def test_database_guard_and_preservation_required(self):
        for key,value in (('database_read_only_enforced',False),('temporary_objects_dropped_on_disconnect',False),('public_tables_checked',91),('public_table_differences',['nav_snapshots']),('temporary_relations',6)):
            p=self.proof();p[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): accept(p)

    def test_image_and_saved_job_must_match(self):
        p={'passed':True,'image':'digest','saved_job_preserved':True,'proof':self.proof()}
        validate_rehearsal(p,'digest')
        with self.assertRaises(ValueError): validate_rehearsal(p,'other')
        p['saved_job_preserved']=False
        with self.assertRaises(ValueError): validate_rehearsal(p,'digest')

    @patch('deploy_upgrade_institutional_security_20261010.runtime.safe_window')
    def test_discovery_schedule_window(self, safe):
        for minute in (1,2,6,7): window('ibkrv2stg-flex-discovery',datetime(2026,10,10,2,minute,20,tzinfo=timezone.utc))
        for minute in (0,3,4,5,8,9):
            with self.subTest(minute=minute),self.assertRaises(ValueError):
                window('ibkrv2stg-flex-discovery',datetime(2026,10,10,2,minute,20,tzinfo=timezone.utc))


if __name__=='__main__': unittest.main()
