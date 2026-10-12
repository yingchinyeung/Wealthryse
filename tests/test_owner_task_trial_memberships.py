"""Observed PG16 two-grantor shape; corrected non-inheritance is source-only."""
import copy
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import run_owner_task_postgres_trial as runner


def members():
    return [dict(member_is_connector=True,bootstrap_grantor=True,self_grantor=False,
        admin_option=True,inherit_option=False,set_option=False),
        dict(member_is_connector=True,bootstrap_grantor=False,self_grantor=True,
            admin_option=False,inherit_option=False,set_option=True)]


class MembershipTests(unittest.TestCase):
    def test_exact_separate_creator_and_set_only_grants(self):
        runner.validate_trial_memberships(members())

    def test_inherited_self_grant_denied(self):
        value=members();value[1]['inherit_option']=True
        with self.assertRaises(ValueError): runner.validate_trial_memberships(value)

    def test_self_admin_denied(self):
        value=members();value[1]['admin_option']=True
        with self.assertRaises(ValueError): runner.validate_trial_memberships(value)

    def test_bootstrap_set_denied(self):
        value=members();value[0]['set_option']=True
        with self.assertRaises(ValueError): runner.validate_trial_memberships(value)

    def test_wrong_member_or_duplicate_denied(self):
        value=members();value[1]['member_is_connector']=False
        for candidate in (value,members()+members()):
            with self.subTest(candidate=candidate),self.assertRaises(ValueError): runner.validate_trial_memberships(candidate)

    def test_boolean_alias_denied(self):
        value=members();value[0]['admin_option']=1
        with self.assertRaises(ValueError): runner.validate_trial_memberships(value)

    def test_explicit_options_and_admin_public_revoke_source(self):
        source=(ROOT/'scripts/run_owner_task_postgres_trial.py').read_text(encoding='utf-8')
        self.assertIn('WITH ADMIN FALSE, INHERIT FALSE, SET TRUE',source)
        start=source.index('with raw.transaction():')
        self.assertLess(source.index("raw.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')",start),
            source.index("raw.execute(sql.SQL('SET LOCAL ROLE {}')",start))
        self.assertIn('a.grantee=0',source)


if __name__ == '__main__':
    unittest.main()
