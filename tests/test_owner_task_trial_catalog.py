"""Exact synthetic cleanup catalog/ACL guards, not live PostgreSQL acceptance."""
import copy
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import run_owner_task_postgres_trial as runner


def acl():
    return [dict(grantee=runner.ROLE,privilege_type=k,grantor=runner.ROLE,is_grantable=False)
        for k in ('CONNECT','CREATE','TEMPORARY')] + [dict(grantee='synthetic_admin',
            privilege_type='CONNECT',grantor=runner.ROLE,is_grantable=False)]


def objects():
    namespaces=[{'nspname': runner.SCHEMA,'trial_owner':True}, {'nspname': 'public','trial_owner':False}]
    relations=[dict(nspname=runner.SCHEMA,relname=name,relkind='r',owner=runner.ROLE,
        index_table=None,index_primary=None,index_unique=None,index_columns=[])
        for name in runner.TABLES]
    specifications=[
        ('tenants',True,True,['tenant_id']),
        ('owner_task_authority_fixture',True,True,['tenant_id','owner_object_id']),
        ('owner_task_worker_fixture',True,True,['tenant_id','worker_id']),
        ('owner_codex_sessions',True,True,['tenant_id','owner_object_id','session_id']),
        ('owner_codex_sessions',False,True,['tenant_id','owner_object_id','idempotency_key']),
        ('owner_codex_tasks',True,True,['tenant_id','owner_object_id','task_id']),
        ('owner_codex_tasks',False,True,['tenant_id','owner_object_id','idempotency_key']),
        ('owner_codex_task_events',True,True,['tenant_id','owner_object_id','task_id','event_no']),
        ('owner_codex_tasks',False,False,['tenant_id','owner_object_id','session_id','created_at','task_id']),
        ('owner_codex_tasks',False,False,['tenant_id','owner_object_id','lease_expires_at']),
    ]
    for position,(table,primary,unique,columns) in enumerate(specifications):
        name = ('owner_codex_tasks_owner_history' if position==8 else
            'owner_codex_tasks_leases' if position==9 else 'synthetic_constraint_index_'+str(position))
        relations.append(dict(nspname=runner.SCHEMA,relname=name,relkind='i',owner=runner.ROLE,
            index_table=table,index_primary=primary,index_unique=unique,index_columns=columns))
    functions=[dict(nspname=runner.SCHEMA,proname=name,owner=runner.ROLE,pronargs=0,
        prokind='f',prosecdef=False,return_type='uuid' if name=='app_tenant_id' else 'trigger')
        for name in runner.FUNCTIONS]
    return namespaces,relations,functions


class TrialDatabaseAclTests(unittest.TestCase):
    def test_exact_owner_issued_non_grantable_acl(self):
        runner.validate_trial_database_acl(acl(),'synthetic_admin')

    def test_wrong_grantor_denied(self):
        rows=acl();rows[0]['grantor']='another_role'
        with self.assertRaises(ValueError): runner.validate_trial_database_acl(rows,'synthetic_admin')

    def test_grant_option_denied(self):
        for value in (True,0,'false'):
            rows=acl();rows[-1]['is_grantable']=value
            with self.subTest(value=value),self.assertRaises(ValueError):
                runner.validate_trial_database_acl(rows,'synthetic_admin')

    def test_extra_recipient_or_duplicate_denied(self):
        for row in (dict(grantee='PUBLIC',privilege_type='CONNECT',grantor=runner.ROLE,is_grantable=False),acl()[0]):
            with self.subTest(row=row),self.assertRaises(ValueError):
                runner.validate_trial_database_acl(acl()+[row],'synthetic_admin')

    def test_owner_connector_or_missing_privilege_denied(self):
        for rows,connector in ((acl(),runner.ROLE),(acl()[:-1],'synthetic_admin')):
            with self.subTest(connector=connector),self.assertRaises(ValueError):
                runner.validate_trial_database_acl(rows,connector)


class TrialObjectTests(unittest.TestCase):
    def test_registered_tables_indexes_and_functions(self):
        runner.validate_trial_objects(*objects())

    def test_pg_lookalike_user_namespace_denied(self):
        namespaces,relations,functions=objects();namespaces.append({'nspname':'pgfoo'})
        with self.assertRaises(ValueError): runner.validate_trial_objects(namespaces,relations,functions)

    def test_schema_owner_or_boolean_alias_denied(self):
        for value in (False,1):
            namespaces,relations,functions=objects();namespaces[0]['trial_owner']=value
            with self.subTest(value=value),self.assertRaises(ValueError):
                runner.validate_trial_objects(namespaces,relations,functions)

    def test_every_unregistered_relation_kind_denied(self):
        for kind in ('S','p','v','m','f','c','I'):
            namespaces,relations,functions=objects();row=copy.deepcopy(relations[0])
            row.update(relname='unregistered',relkind=kind);relations.append(row)
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                runner.validate_trial_objects(namespaces,relations,functions)

    def test_public_object_or_wrong_owner_denied(self):
        for key,value in (('nspname','public'),('owner','another_owner')):
            namespaces,relations,functions=objects();relations[0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                runner.validate_trial_objects(namespaces,relations,functions)

    def test_extra_index_or_changed_columns_denied(self):
        for mode in ('extra','columns','target','named_secondary','primary_type'):
            namespaces,relations,functions=objects()
            if mode=='extra': relations.append(copy.deepcopy(relations[-1]))
            elif mode=='columns': relations[-1]['index_columns']=['tenant_id']
            elif mode=='target': relations[-1]['index_table']='unregistered'
            elif mode=='named_secondary': relations[-1]['relname']='unregistered'
            else: relations[-1]['index_primary']=0
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                runner.validate_trial_objects(namespaces,relations,functions)

    def test_missing_or_duplicate_table_denied(self):
        for mode in ('missing','duplicate'):
            namespaces,relations,functions=objects()
            if mode=='missing': relations.pop(0)
            else: relations[0]=copy.deepcopy(relations[1])
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                runner.validate_trial_objects(namespaces,relations,functions)

    def test_extra_or_missing_function_denied(self):
        for mode in ('extra','missing'):
            namespaces,relations,functions=objects()
            if mode=='extra': functions.append(copy.deepcopy(functions[0]))
            else: functions.pop()
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                runner.validate_trial_objects(namespaces,relations,functions)

    def test_public_overloaded_definer_or_wrong_return_function_denied(self):
        for key,value in (('nspname','public'),('pronargs',1),('pronargs',False),
                ('prosecdef',True),('return_type','text'),('owner','another_owner'),('prokind','p')):
            namespaces,relations,functions=objects();functions[0][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                runner.validate_trial_objects(namespaces,relations,functions)

    def test_cleanup_queries_are_not_table_only_and_guard_precedes_drop(self):
        source=(ROOT/'scripts/run_owner_task_postgres_trial.py').read_text(encoding='utf-8')
        cleanup=source[source.index('def cleanup('):source.index('def main(')]
        self.assertNotIn("NOT LIKE 'pg_%'",cleanup)
        self.assertNotIn('c.relkind IN',cleanup)
        self.assertIn("nspname !~ '^pg_'",cleanup)
        self.assertIn('FROM pg_proc',cleanup)
        self.assertLess(cleanup.index('validate_trial_objects('),cleanup.index("sql.SQL('DROP DATABASE {}')"))
        self.assertNotIn('pg_terminate_backend',cleanup)


if __name__ == '__main__':
    unittest.main()
