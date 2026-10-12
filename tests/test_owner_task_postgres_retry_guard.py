"""A reviewed retry cannot broaden the frozen SQL trial payload."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from owner_task_postgres_retry_guard import validate_payload_change


def original_payload():
    value = ''
    for name in ('util','db','owner_codex_tasks'):
        value += "m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl'\n"
    value += "h=types.ModuleType('owner_task_sql_acceptance');sys.modules[h.__name__]=h\n"
    value += 'SELECT c.relname,nf.nspname AS function_schema,fp.proname\n'
    value += 'unchanged-reviewed-SQL-and-wrapper\n'
    return value


def corrected_payload():
    value = original_payload()
    for name in ('util','db','owner_codex_tasks'):
        before = "m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl'\n"
        value = value.replace(before,before.rstrip('\n')+";m.__file__='/app/src/ibkr_etl/"+name+".py'\n")
    return value.replace("h=types.ModuleType('owner_task_sql_acceptance');sys.modules[h.__name__]=h\n",
        "h=types.ModuleType('owner_task_sql_acceptance');h.__file__='/app/scripts/verify_owner_task_postgres.py';sys.modules[h.__name__]=h\n").replace(
        'SELECT c.relname,nf.nspname AS function_schema,fp.proname','SELECT DISTINCT c.relname,nf.nspname AS function_schema,fp.proname')


class RetryGuardTests(unittest.TestCase):
    def test_exact_reviewed_changes_permitted(self):
        validate_payload_change(original_payload(),corrected_payload())

    def test_unchanged_broken_payload_denied(self):
        with self.assertRaises(ValueError): validate_payload_change(original_payload(),original_payload())

    def test_additional_change_denied(self):
        with self.assertRaises(ValueError): validate_payload_change(original_payload(),corrected_payload()+'new-command\n')

    def test_duplicate_initializer_denied(self):
        with self.assertRaises(ValueError): validate_payload_change(original_payload()+original_payload(),corrected_payload())

    def test_missing_query_denied(self):
        with self.assertRaises(ValueError): validate_payload_change(original_payload().replace('SELECT c.relname','SELECT other'),corrected_payload())


if __name__ == '__main__':
    unittest.main()
