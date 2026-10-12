import json
import unittest
from datetime import datetime, timezone
from ibkr_etl.specialist_audit import append_event

TENANT='00000000-0000-0000-0000-000000000001'


class Result:
    def __init__(self,row=None): self.row=row
    def fetchone(self): return self.row


class ReaderWriter:
    def __init__(self): self.events=[]; self.statements=[]
    def execute(self,sql,params=()):
        self.statements.append(sql)
        if 'pg_advisory_xact_lock' in sql: return Result()
        if 'app_tenant_id()' in sql: return Result({'tenant_id':TENANT})
        if 'SELECT event_json' in sql:
            found=next((event for event in self.events if event['event_id']==params[1]),None)
            return Result({'event_json':found} if found else None)
        if 'SELECT sequence_no' in sql:
            return Result({'sequence_no':self.events[-1]['sequence'],'event_sha256':self.events[-1]['sha256']} if self.events else None)
        if 'clock_timestamp()' in sql: return Result({'observed_at':datetime(2026,10,2,10,tzinfo=timezone.utc)})
        if 'INSERT INTO specialist_audit_events' in sql:
            self.events.append(json.loads(params[3])); return Result()
        raise AssertionError('Unexpected query')


class SpecialistAuditTests(unittest.TestCase):
    def setUp(self):
        self.db=ReaderWriter()
        self.fields=dict(tenant_id=TENANT,event_id='one',role='data_analyst',action='inspect_and_analyze',
            operation_id='op-one',phase='started',evidence_sha256='a'*64,cost_microusd=0)
    def test_append_and_retry_produce_one_record(self):
        first=append_event(self.db,**self.fields)
        self.assertEqual(first,append_event(self.db,**self.fields))
        self.assertEqual(len(self.db.events),1)
    def test_next_event_hash_chains_to_previous(self):
        first=append_event(self.db,**self.fields)
        second=append_event(self.db,**dict(self.fields,event_id='two',phase='succeeded'))
        self.assertEqual(second['sequence'],2)
        self.assertEqual(second['previous_sha256'],first['sha256'])
    def test_conflicting_retry_does_not_overwrite(self):
        append_event(self.db,**self.fields)
        with self.assertRaises(ValueError):
            append_event(self.db,**dict(self.fields,phase='failed'))
        self.assertEqual(self.db.events[0]['phase'],'started')
    def test_boolean_cost_is_not_equivalent_to_number_on_retry(self):
        append_event(self.db,**self.fields)
        with self.assertRaises(ValueError):
            append_event(self.db,**dict(self.fields,cost_microusd=False))
    def test_cross_tenant_denied_before_data_lookup(self):
        with self.assertRaises(ValueError):
            append_event(self.db,**dict(self.fields,tenant_id='other'))
        self.assertEqual(len(self.db.events),0)
        self.assertFalse(any('SELECT event_json' in s for s in self.db.statements))
    def test_invalid_metadata_is_never_inserted(self):
        for changes in ({'role':'owner'},{'phase':'unknown'},{'cost_microusd':-1},{'action':'run shell code'}, {'evidence_sha256':'invalid'}):
            with self.assertRaises(ValueError):
                append_event(self.db,**dict(self.fields,**changes))
        self.assertEqual(self.db.events,[])
    def test_writer_never_commits_or_updates_caller_transaction(self):
        append_event(self.db,**self.fields)
        self.assertTrue(all(s.strip().startswith(('SELECT','INSERT')) for s in self.db.statements))


if __name__=='__main__':
    unittest.main()
