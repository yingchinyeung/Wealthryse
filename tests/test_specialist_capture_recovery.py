import base64
import gzip
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import capture_specialist_recovery_check as recovery
import run_specialist_capture as deployment
from archive_specialist_acceptance import verify_interrupted_predecessor

OP='d8d57217-1d5e-4e34-8fe9-89b377135c72'


class RecoveryTests(unittest.TestCase):
    def test_interruption_never_calls_anchor_writer(self):
        archive=Mock()
        with self.assertRaises(recovery.PlannedInterruption):
            recovery.InterruptBeforeAnchor(archive).put(recovery.TENANT,{'phase':'started'})
        archive.put.assert_not_called()

    def test_fresh_execution_reports_committed_interruption_not_success(self):
        c=Mock();c.execute.return_value.fetchall.side_effect=[[],[{'event_json':{'phase':'started','sequence':7,'sha256':'a'*64}}]]
        with patch.object(recovery,'bind_audit_session'),patch.object(recovery,'capture_permission_inspection',side_effect=recovery.PlannedInterruption):
            result=recovery.run(c,Mock(),OP,'b'*64)
        self.assertEqual(result['status'],'interrupted')
        self.assertTrue(result['database_committed'])
        self.assertFalse(result['protected_archive_verified'])

    def test_new_worker_uses_retained_state_without_fault_wrapper(self):
        c=Mock();c.execute.return_value.fetchall.return_value=[{'event_json':{'phase':'started','sha256':'a'*64}}]
        archive=Mock()
        with patch.object(recovery,'bind_audit_session'),patch.object(recovery,'capture_permission_inspection',return_value={'status':'passed','phases':[{'event_sha256':'a'*64}]}) as capture:
            result=recovery.run(c,archive,OP,'b'*64)
        self.assertIs(capture.call_args.args[1],archive)
        self.assertTrue(result['resumed_committed_intent'])

    def test_changed_intent_rejected(self):
        c=Mock();c.execute.return_value.fetchall.return_value=[{'event_json':{'phase':'started','sha256':'a'*64}}]
        with patch.object(recovery,'bind_audit_session'),patch.object(recovery,'capture_permission_inspection',return_value={'phases':[{'event_sha256':'c'*64}]}):
            with self.assertRaises(ValueError): recovery.run(c,Mock(),OP,'b'*64)

    def test_rehearsal_payload_distinct_and_deterministic(self):
        normal=deployment.bundle(OP);trial=deployment.bundle(OP,True)
        self.assertNotEqual(normal[2],trial[2]);self.assertEqual(trial,deployment.bundle(OP,True))
        source=gzip.decompress(base64.b64decode(trial[0][1].split("b64decode('")[1].split("')")[0])).decode()
        compile(source,'recovery','exec')
        self.assertIn('InterruptBeforeAnchor',source)
        self.assertLess(len(trial[0][1]),30000)

    def test_receipt_binds_both_workers_and_original_committed_hash(self):
        dispatch=dict(execution='second',recovery_rehearsal=True,operation_id=OP,payload_sha256='a'*64,
                      source_manifest_sha256='b'*64,job_settings_sha256='c'*64)
        previous={**dispatch,'execution':'first'}
        stopped=dict(status='interrupted',expected_interruption=True,operation_id=OP,database_committed=True,
                     protected_archive_verified=False,sequence=3,event_sha256='d'*64)
        prior=dict(execution='first',status='Failed',configuration_verified=True,job_settings_unchanged=True,
                   payload_sha256='a'*64,records=[stopped])
        result={'records':[dict(resumed_committed_intent=True,operation_event_count=2,
                               phases=[dict(sequence=3,event_sha256='d'*64)])]}
        verify_interrupted_predecessor(dispatch,result,previous,prior)
        for changed in ({'execution':'first'},{'payload_sha256':'f'*64},{'operation_id':'different'}):
            with self.assertRaises(ValueError): verify_interrupted_predecessor({**dispatch,**changed},result,previous,prior)
        with self.assertRaises(ValueError):
            verify_interrupted_predecessor(dispatch,result,previous,{**prior,'records':[{**stopped,'event_sha256':'f'*64}]})


if __name__=='__main__': unittest.main()
