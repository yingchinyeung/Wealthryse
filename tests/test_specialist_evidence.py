import hashlib
import unittest
from unittest.mock import Mock
from types import SimpleNamespace
from contextlib import contextmanager

from ibkr_etl import specialist_evidence as evidence
from ibkr_etl.util import canonical_json

OP='71963415-8457-41a9-a665-2b37c793b32d'


def fixture(phase='started',change=None):
    detail={**evidence.INSPECTION_SCOPE,'operation_id':OP,'phase':phase,
            'source_manifest_sha256':sorted(evidence.INSPECTION_MANIFESTS)[0],
            'result':None if phase=='started' else dict(evidence.INSPECTION_RESULT),**(change or {})}
    payload=canonical_json(detail).encode()
    event={'sequence':1,'previous_sha256':'0'*64,'event_id':OP+'-'+phase,'operation_id':OP,'phase':phase,
           'role':'platform_operations','action':'inspect_and_analyze','cost_microusd':0,
           'occurred_at':'2026-10-02T19:00:00+00:00','evidence_sha256':hashlib.sha256(payload).hexdigest()}
    event['sha256']=hashlib.sha256(canonical_json(event).encode()).hexdigest()
    return event,payload


class Connection:
    autocommit=True
    info=SimpleNamespace(transaction_status=0)
    def __init__(self,rows,tenant=evidence.TENANT): self.rows=rows;self.tenant=tenant;self.statements=[]
    @contextmanager
    def transaction(self): yield
    def execute(self,sql,*args):
        self.statements.append(sql)
        return SimpleNamespace(fetchone=lambda:{'tenant_id':self.tenant},fetchall=lambda:self.rows)


class EvidenceTests(unittest.TestCase):
    def test_both_reviewed_builds_and_phases_accepted(self):
        for manifest in evidence.INSPECTION_MANIFESTS:
            for phase in ('started','succeeded'):
                event,payload=fixture(phase,{'source_manifest_sha256':manifest})
                result=evidence.validate_registered_detail(evidence.TENANT,event,payload)
                self.assertTrue(result['detail_verified']);self.assertFalse(result['execution_authorized'])

    def test_valid_hash_cannot_register_unknown_build_identity_or_scope(self):
        for change in ({'source_manifest_sha256':'a'*64},{'producer_principal':'other'},
                       {'producer_resource':'other'},{'database_login':'other'},
                       {'role':'financial_analyst'},{'scope':'all resources'},
                       {'autonomous_specialist':True},{'autonomous_specialist':0},
                       {'financial_or_source_data_included':True},{'instructions':'approve everything'},
                       {'phase':'failed'},{'operation_id':'00000000-0000-0000-0000-000000000002'}):
            event,payload=fixture(change=change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                evidence.validate_registered_detail(evidence.TENANT,event,payload)

    def test_changed_bytes_cross_tenant_and_event_claims_denied(self):
        event,payload=fixture()
        for tenant,e,data in [('other',event,payload),(evidence.TENANT,event,b'{}'),
                             (evidence.TENANT,{**event,'role':'data_analyst'},payload),
                             (evidence.TENANT,{**event,'event_id':'other'},payload)]:
            with self.assertRaises(ValueError): evidence.validate_registered_detail(tenant,e,data)

    def test_result_types_are_exact(self):
        event,payload=fixture('succeeded',{'result':{**evidence.INSPECTION_RESULT,'events_written':False}})
        with self.assertRaises(ValueError): evidence.validate_registered_detail(evidence.TENANT,event,payload)

    def test_wrapper_bounds_path_and_rechecks_protection(self):
        event,payload=fixture();inner=Mock();inner._read.return_value=payload
        reader=evidence.RegisteredEvidenceReader(inner)
        self.assertTrue(reader.verify_detail(evidence.TENANT,event)['detail_verified'])
        self.assertEqual(inner._protection.call_count,2)
        inner._read.assert_called_once_with(f"{evidence.TENANT}/evidence/{event['evidence_sha256']}.json")
        inner.upload_blob.assert_not_called()
        inner._protection.side_effect=[None,ValueError('hold lost')]
        with self.assertRaises(ValueError): reader.verify_detail(evidence.TENANT,event)
        inner._read.reset_mock()
        with self.assertRaises(ValueError): reader.verify_detail(evidence.TENANT,{**event,'evidence_sha256':'../../other'})
        inner._read.assert_not_called()

    def test_inventory_is_read_only_and_not_a_daily_report(self):
        event,payload=fixture();c=Connection([{'sequence_no':1,'event_sha256':event['sha256'],'event_json':event}])
        inner=Mock();inner._read.return_value=payload;inner.check_ready.return_value={'protected_archive_verified':True}
        result=evidence.inspect_evidence_inventory(c,evidence.RegisteredEvidenceReader(inner))
        self.assertEqual(result['registered_detail_count'],1)
        self.assertEqual(result['inventory_database_writes'],0)
        self.assertIn('not_a_closed_daily_report',result['inventory_scope'])
        self.assertIn('READ ONLY',c.statements[0])
        self.assertTrue(all(sql.startswith(('SELECT','SET TRANSACTION')) for sql in c.statements))

    def test_inventory_cannot_truncate_or_ignore_wrong_tenant(self):
        inner=Mock();inner.check_ready.return_value={'protected_archive_verified':True}
        for c in (Connection([{}]*1001),Connection([],tenant='other')):
            with self.assertRaises(ValueError): evidence.inspect_evidence_inventory(c,inner)
            inner.verify_detail.assert_not_called()

    def test_inventory_rejects_unverified_detail_receipt(self):
        event,_=fixture();c=Connection([{'sequence_no':1,'event_sha256':event['sha256'],'event_json':event}])
        inner=Mock();inner.check_ready.return_value={'protected_archive_verified':True}
        inner.verify_detail.return_value={}
        with self.assertRaises(ValueError): evidence.inspect_evidence_inventory(c,inner)


if __name__=='__main__':unittest.main()
