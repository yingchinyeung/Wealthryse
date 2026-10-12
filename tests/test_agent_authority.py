from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from unittest.mock import Mock,patch
from pathlib import Path
import unittest

from ibkr_etl.agent_authority import PostgresAuthoritySource,decode_authority
from ibkr_etl.agent_governance import POLICY_VERSION

TENANT='00000000-0000-0000-0000-000000000001'
PRINCIPAL='60313d53-6387-46d4-9bb8-dc5f8ab925a5'
NOW=datetime(2026,10,2,tzinfo=timezone.utc)


def row(**changes):
    return dict(tenant_id=TENANT,principal_id=PRINCIPAL,specialist_role='data_analyst',subject_ids=['account-a'],
                resource_ids=[],tool_names=['get_portfolio_summary'],enabled=True,background_allowed=False,
                expires_at=NOW+timedelta(hours=1),observed_at=NOW,policy_version=POLICY_VERSION,revision=1,
                control_policy_version=POLICY_VERSION,control_revision=1,kill_switch=False,**changes)


def decode(value): return decode_authority(value,tenant_id=TENANT,principal_id=PRINCIPAL)


class AuthorityTests(unittest.TestCase):
    def test_registered_exact_scope(self):
        state=decode(row());self.assertFalse(state.kill_switch)
        self.assertEqual(state.grant.subject_ids,frozenset({'account-a'}))
        self.assertFalse(state.grant.background_allowed)

    def test_missing_disabled_expired_or_stopped_is_disabled(self):
        self.assertTrue(decode(None).kill_switch)
        for changes in ({'enabled':False},{'kill_switch':True},{'expires_at':NOW},{'policy_version':'old'},
                        {'control_policy_version':'old'}):
            self.assertIsNone(decode({**row(),**changes}).grant)

    def test_identity_role_types_and_scope_cannot_expand(self):
        for changes in ({'tenant_id':'other'},{'principal_id':'other'},{'enabled':1},{'kill_switch':0},
                        {'specialist_role':'owner'},{'subject_ids':['*']},{'tool_names':['execute_sql']},
                        {'subject_ids':['account-a','account-a']},{'subject_ids':[]},
                        {'subject_ids':['a']*501},{'resource_ids':['https://example.com']},
                        {'expires_at':NOW.replace(tzinfo=None)},{'revision':True}):
            with self.subTest(changes=changes),self.assertRaises(ValueError): decode({**row(),**changes})

    def test_fresh_connection_each_time_and_revocation_not_cached(self):
        db=Mock();db.execute.return_value.fetchone.side_effect=[row(),{**row(),'enabled':False}]
        opened=[]
        @contextmanager
        def factory(tenant): opened.append(tenant);yield db
        authority=PostgresAuthoritySource(factory)
        with patch('ibkr_etl.agent_authority._guard_database') as guard:
            self.assertIsNotNone(authority.load_current(tenant_id=TENANT,principal_id=PRINCIPAL).grant)
            self.assertIsNone(authority.load_current(tenant_id=TENANT,principal_id=PRINCIPAL).grant)
        self.assertEqual(len(opened),2);self.assertEqual(guard.call_count,2)
        self.assertTrue(all(c.args[0].startswith('SELECT') for c in db.execute.call_args_list))

    def test_invalid_caller_or_reader_rejected_before_authority_query(self):
        factory=Mock();authority=PostgresAuthoritySource(factory)
        with self.assertRaises(ValueError): authority.load_current(tenant_id='bad',principal_id=PRINCIPAL)
        factory.assert_not_called()

    def test_schema_is_dormant_isolated_and_grants_no_access(self):
        sql=(Path(__file__).resolve().parents[1]/'migrations/postgres/058_specialist_observation_authority.sql').read_text()
        self.assertEqual(sql.count('FORCE ROW LEVEL SECURITY'),2)
        self.assertIn('kill_switch BOOLEAN NOT NULL DEFAULT TRUE',sql)
        self.assertIn('enabled BOOLEAN NOT NULL DEFAULT FALSE',sql)
        self.assertNotIn('INSERT INTO',sql)
        self.assertNotIn('GRANT ',sql)


if __name__=='__main__':unittest.main()
