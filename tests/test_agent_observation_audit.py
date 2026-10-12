from contextlib import contextmanager
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import json
import unittest

from ibkr_etl import agent_observation_audit as audit
from ibkr_etl.util import canonical_json
from ibkr_etl.agent_observation_evidence import ObservationEvidenceBinding,AGENT_PRINCIPAL

OP='f6b76b3a-8140-482e-8b7d-eeb85d2a7191'
ACCESS={'database_access':'verified','source_table_access':False,'source_mutation_authority':False,'ledger_append_only':True}


def detail(phase='started'):
    return dict(schema=audit.SCHEMA,tenant_id=audit.TENANT,operation_id=OP,role='data_analyst',
        action='inspect_and_analyze',phase=phase,tool_name='get_performance_series',subject_id='account-a',
        agent_sha256=hashlib.sha256(AGENT_PRINCIPAL.encode()).hexdigest(),user_sha256='b'*64,request_sha256='c'*64,
        response_sha256='d'*64 if phase=='succeeded' else None,code=None,policy_version=audit.POLICY_VERSION,
        tool_attempted=phase=='succeeded',result_delivery_confirmed=False)


class Ledger:
    autocommit=True
    info=SimpleNamespace(transaction_status=0)
    def __init__(self): self.events=[];self.commits=0
    @contextmanager
    def transaction(self):
        before=list(self.events)
        try: yield
        except Exception:
            self.events=before;raise
        else: self.commits+=1
    def execute(self,sql,args=()):
        row=None
        if 'pg_advisory_xact_lock' in sql: pass
        elif 'app_tenant_id()' in sql: row={'tenant_id':audit.TENANT}
        elif 'SELECT event_json' in sql:
            value=next((e for e in self.events if e['event_id']==args[1]),None)
            row={'event_json':value} if value else None
        elif 'SELECT sequence_no' in sql:
            row={'sequence_no':self.events[-1]['sequence'],'event_sha256':self.events[-1]['sha256']} if self.events else None
        elif 'clock_timestamp()' in sql: row={'observed_at':datetime(2026,10,2,tzinfo=timezone.utc)}
        elif 'INSERT INTO specialist_audit_events' in sql: self.events.append(json.loads(args[3]))
        else: raise AssertionError('Unexpected audit query')
        return SimpleNamespace(fetchone=lambda:row)


class Archive:
    def __init__(self,ledger): self.ledger=ledger;self.blobs={};self.fail=False
    def _retain(self,name,payload):
        if self.fail: raise ConnectionError('archive unavailable')
        if name in self.blobs and self.blobs[name]!=payload: raise ValueError('collision')
        self.blobs[name]=payload
        return {'sha256':hashlib.sha256(payload).hexdigest(),'protected_archive_verified':True}
    def put(self,tenant,event):
        assert self.ledger.commits>0
        return self._retain(f'{tenant}/events/{event["sequence"]}.json',canonical_json(event).encode())


class ObservationAuditTests(unittest.TestCase):
    def setUp(self):
        self.ledger=Ledger();self.archive=Archive(self.ledger)
        @contextmanager
        def factory(): yield self.ledger
        self.producer=ObservationEvidenceBinding('a'*64)
        patch('ibkr_etl.specialist_evidence.OBSERVATION_MANIFESTS',frozenset({'a'*64})).start()
        self.adapter=audit.ProtectedObservationAudit(factory,self.archive,producer=self.producer)
        self.bind=patch.object(audit,'bind_audit_session').start()
        patch.object(audit,'verify_audit_access',return_value=ACCESS).start()
        self.addCleanup(patch.stopall)

    def test_real_append_commit_and_archive_handoff_with_exact_replay(self):
        first=self.adapter.record(detail());again=self.adapter.record(detail())
        audit.verify_receipt(first,detail());self.assertEqual(first,again)
        done=self.adapter.record(detail('succeeded'));audit.verify_receipt(done,detail('succeeded'))
        self.assertEqual(len(self.ledger.events),2)
        self.assertEqual(done['event']['previous_sha256'],first['event']['sha256'])
        self.assertEqual(len(self.archive.blobs),4)

    def test_changed_same_operation_intent_cannot_rewrite_history(self):
        self.adapter.record(detail())
        with self.assertRaises(ValueError): self.adapter.record({**detail(),'subject_id':'account-b'})
        self.assertEqual(len(self.ledger.events),1)
        self.assertEqual(self.ledger.events[0]['evidence_sha256'],hashlib.sha256(canonical_json(self.producer.envelope(detail())).encode()).hexdigest())

    def test_detail_failure_precedes_database_insert(self):
        self.archive.fail=True
        with self.assertRaises(ConnectionError): self.adapter.record(detail())
        self.assertEqual(self.ledger.events,[])

    def test_committed_event_archive_failure_recovers_without_duplicates(self):
        original=self.archive.put
        self.archive.put=lambda *args: (_ for _ in ()).throw(ConnectionError('archive missing'))
        with self.assertRaises(ConnectionError): self.adapter.record(detail())
        self.assertEqual(len(self.ledger.events),1)
        original_event=dict(self.ledger.events[0]);self.archive.put=original
        result=self.adapter.record(detail());audit.verify_receipt(result,detail())
        self.assertEqual(self.ledger.events,[original_event])

    def test_privileged_or_unverified_audit_writer_rejected(self):
        with patch.object(audit,'verify_audit_access',return_value={**ACCESS,'source_mutation_authority':True}):
            with self.assertRaises(ValueError): self.adapter.record(detail())
        self.assertEqual(self.archive.blobs,{})

    def test_schema_rejects_raw_data_unregistered_action_and_false_outcome(self):
        for changes in ({'prompt':'untrusted'},{'role':'owner'},{'action':'overwrite_ibkr_source'},
                        {'tool_name':'execute_sql'},{'tenant_id':'other'},{'tool_attempted':True},
                        {'response_sha256':'a'*64},{'result_delivery_confirmed':True},{'subject_id':'bad\n'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError): self.adapter.record({**detail(),**changes})
        self.bind.assert_not_called()

    def test_safe_failure_without_original_exception_body(self):
        failed={**detail(),'phase':'failed','code':'tool_unavailable','tool_attempted':True}
        audit.verify_receipt(self.adapter.record(failed),failed)
        with self.assertRaises(ValueError): self.adapter.record({**failed,'code':'password=secret'})

    def test_receipt_must_prove_commit_and_exact_event(self):
        receipt=self.adapter.record(detail())
        for change in ({'database_committed':False},{'protected_archive_verified':False},
                       {'event':{**receipt['event'],'evidence_sha256':'f'*64}}):
            with self.assertRaises(ValueError): audit.verify_receipt({**receipt,**change},detail())


if __name__=='__main__':unittest.main()
