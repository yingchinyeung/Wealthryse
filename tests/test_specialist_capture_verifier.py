import hashlib
import unittest
from unittest.mock import Mock,patch
from ibkr_etl import specialist_capture_verifier as verifier
from ibkr_etl.util import canonical_json

OP='d8d57217-1d5e-4e34-8fe9-89b377135c72'


def fixtures(detail_change=None):
    rows=[];blobs={};previous='0'*64
    for seq,phase in enumerate(('started','succeeded'),1):
        result=None if seq==1 else {'database_access':'verified','reachable_roles_verified':True,
            'source_table_access':False,'source_mutation_authority':False,'ledger_append_only':True,'events_written':0}
        detail={'schema':'audit-permission-inspection-v1','operation_id':OP,'tenant_id':verifier.TENANT,
            'phase':phase,'source_manifest_sha256':'b'*64,'autonomous_specialist':False,
            'producer_kind':'trusted_manual_rollout_worker','producer_principal':'60313d53-6387-46d4-9bb8-dc5f8ab925a5',
            'database_login':'ibkr_specialist_audit_login','role':'platform_operations','action':'inspect_and_analyze',
            'producer_resource':'/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11/providers/Microsoft.App/jobs/ibkrv2stg-agent-audit',
            'financial_or_source_data_included':False,'result':result,**(detail_change or {})}
        payload=canonical_json(detail).encode();digest=hashlib.sha256(payload).hexdigest()
        blobs[f'{verifier.TENANT}/evidence/{digest}.json']=payload
        e={'event_id':OP+'-'+phase,'operation_id':OP,'phase':phase,'role':'platform_operations','action':'inspect_and_analyze',
           'sequence':seq,'previous_sha256':previous,'evidence_sha256':digest,'occurred_at':f'2026-10-02T19:00:0{seq}+00:00','cost_microusd':0}
        e['sha256']=hashlib.sha256(canonical_json(e).encode()).hexdigest();previous=e['sha256']
        rows.append({'sequence_no':seq,'event_sha256':e['sha256'],'event_json':e})
    c=Mock();c.execute.return_value.fetchall.return_value=rows
    archive=Mock();archive.check_ready.return_value={'protected_archive_verified':True};archive._read.side_effect=blobs.__getitem__
    return c,archive,rows,blobs


class IndependentReaderTests(unittest.TestCase):
    def run_check(self,c,archive):
        with patch.object(verifier,'bind_report_session'),patch.object(verifier,'verify_report_access'):
            return verifier.verify_capture(c,archive,operation_id=OP,manifest_hash='b'*64)

    def test_independent_reader_verifies_both_events_and_details(self):
        c,a,_,_=fixtures();result=self.run_check(c,a)
        self.assertTrue(result['independent_reader_verified'])
        self.assertEqual(a.verify.call_count,2);self.assertEqual(a._read.call_count,2)
        self.assertEqual(result['database_writes'],0)
        self.assertTrue(all(call.args[0].lstrip().startswith('SELECT') for call in c.execute.call_args_list))

    def test_missing_phase_or_altered_event_rejected(self):
        c,a,rows,_=fixtures();rows.pop()
        with self.assertRaises(ValueError): self.run_check(c,a)
        c,a,rows,_=fixtures();rows[1]['event_json']['evidence_sha256']='c'*64
        with self.assertRaises(ValueError): self.run_check(c,a)

    def test_valid_hash_cannot_hide_wrong_identity_or_source(self):
        for change in ({'producer_principal':'other'},{'source_manifest_sha256':'c'*64},{'autonomous_specialist':True},
                       {'role':'financial_analyst'},{'producer_resource':'other'},{'financial_or_source_data_included':True}):
            c,a,_,_=fixtures(change)
            with self.subTest(change=change),self.assertRaises(ValueError): self.run_check(c,a)

    def test_changed_detail_bytes_fail_hash_check(self):
        c,a,_,_=fixtures();a._read.side_effect=None;a._read.return_value=b'{}'
        with self.assertRaises(ValueError): self.run_check(c,a)

    def test_archive_loss_cannot_be_acknowledged(self):
        c,a,_,_=fixtures();a.check_ready.side_effect=[{'protected_archive_verified':True},{'protected_archive_verified':False}]
        with self.assertRaises(ValueError): self.run_check(c,a)


if __name__=='__main__':unittest.main()
