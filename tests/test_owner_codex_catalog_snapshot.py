"""Synthetic diagnostic contract only; no live catalog reconciliation claimed."""
import copy
import json
from types import SimpleNamespace
import unittest
from ibkr_etl import owner_codex_catalog_snapshot as s
import qualify_owner_task_catalog_diagnostic_source as qualification


def fixture():
    roles=[dict(oid=i,name='synthetic-role-'+str(i),superuser=False,inherit=False,create_role=False,
        create_database=False,can_login=False,replication=False,bypass_rls=False,connection_limit=-1,
        valid_until=None,configuration_sha256=None,marker_sha256=None) for i in (1,2,3)]
    db=[dict(oid=10,name='synthetic-db',owner_oid=1,allow_connections=True,connection_limit=-1,
        acl_sha256='a'*64,marker_sha256='b'*64)]
    memberships=[dict(role_oid=1,member_oid=2,grantor_oid=i,admin_option=i==1,
        inherit_option=False,set_option=True) for i in (1,3)]
    return dict(databases=db,roles=roles,memberships=memberships)


class SnapshotTests(unittest.TestCase):
    def test_same_member_role_distinct_grantors_stable_in_any_order(self):
        before=fixture();after=copy.deepcopy(before)
        after['memberships'].reverse();after['roles'].reverse()
        left,right=s.CatalogSnapshot.from_rows(before),s.CatalogSnapshot.from_rows(after)
        self.assertEqual(left.sha256,right.sha256)
        self.assertEqual(s.differences(left,right),{})

    def test_actual_permission_grantor_and_config_changes_are_detected(self):
        for component,key,value in (('memberships','admin_option',False),
            ('memberships','grantor_oid',2),('roles','configuration_sha256','c'*64),
            ('roles','can_login',True),('databases','acl_sha256','d'*64)):
            before=fixture();after=copy.deepcopy(before);after[component][0][key]=value
            left,right=s.CatalogSnapshot.from_rows(before),s.CatalogSnapshot.from_rows(after)
            with self.subTest(component=component,key=key):
                self.assertNotEqual(left.sha256,right.sha256)
                self.assertIn(component,s.differences(left,right))

    def test_duplicate_keys_not_silently_deduplicated(self):
        data=fixture();data['memberships'].append(copy.deepcopy(data['memberships'][0]))
        with self.assertRaises(s.CatalogSnapshotError):s.CatalogSnapshot.from_rows(data)

    def test_malformed_values_have_fixed_sanitized_errors(self):
        for value in ('\ud800', 'credential-like-value'*10, None):
            data=fixture();data['roles'][0]['name']=value
            with self.subTest(value=value),self.assertRaises(s.CatalogSnapshotError) as caught:
                s.CatalogSnapshot.from_rows(data)
            self.assertEqual(str(caught.exception),'owner_catalog_snapshot_unverified')

    def test_unknown_fields_missing_grantor_and_invalid_references_rejected(self):
        for mode in ('unknown','missing','reference','bool_oid'):
            data=fixture()
            if mode=='unknown':data['roles'][0]['password']='must-not-log'
            if mode=='missing':data['memberships'][0].pop('grantor_oid')
            if mode=='reference':data['memberships'][0]['grantor_oid']=999
            if mode=='bool_oid':data['roles'][0]['oid']=True
            with self.subTest(mode=mode),self.assertRaises(s.CatalogSnapshotError):
                s.CatalogSnapshot.from_rows(data)

    def test_truncation_is_not_success(self):
        data=fixture();data['memberships']*=4097
        with self.assertRaises(s.CatalogSnapshotError):s.CatalogSnapshot.from_rows(data)

    def test_snapshot_bytes_are_immutable_and_canonical(self):
        data=fixture();snap=s.CatalogSnapshot.from_rows(data);sha=snap.sha256
        data['roles'][0]['can_login']=True;rows=snap.rows();rows['roles'][0]['can_login']=True
        self.assertEqual(snap.sha256,sha)
        with self.assertRaises(s.CatalogSnapshotError):s.CatalogSnapshot(json.dumps(fixture()).encode())

    def test_differences_emit_keys_and_hashes_not_configuration_or_names(self):
        before=fixture();after=copy.deepcopy(before);after['roles'][0]['can_login']=True
        result=s.differences(s.CatalogSnapshot.from_rows(before),s.CatalogSnapshot.from_rows(after))
        self.assertEqual(result['roles'][0]['changed_fields'],['can_login'])
        self.assertNotIn('synthetic-role',json.dumps(result))
        self.assertEqual(len(result['roles'][0]['before_sha256']),64)

    def test_queries_are_fixed_bounded_catalog_selects_and_redact_settings(self):
        self.assertTrue(all(q.startswith('SELECT ') and 'pg_catalog.' in q and ' LIMIT ' in q for q in s.QUERIES.values()))
        self.assertIn('m.grantor',s.QUERIES['memberships'])
        self.assertIn('m.roleid,m.member,m.grantor',s.QUERIES['memberships'])
        self.assertNotIn('rolpassword',' '.join(s.QUERIES.values()))
        self.assertIn('sha256',s.QUERIES['roles'])

    def test_wrong_host_database_login_tls_and_active_transaction_rejected(self):
        for change in (dict(host='other-host'),dict(dbname='ibkr_staging'),dict(user='other'),dict(transaction_status=2)):
            info=dict(host=s.host.HOST,dbname='postgres',user=s.host.ADMIN,transaction_status=0,
                get_parameters=lambda:dict(sslmode='verify-full',sslrootcert=s.CA_PATH))
            info.update(change)
            c=SimpleNamespace(autocommit=True,info=SimpleNamespace(**info),pgconn=SimpleNamespace(ssl_in_use=True))
            with self.subTest(change=change),self.assertRaises(s.CatalogSnapshotError):s.read_snapshot(c)

    def test_read_only_boundary_and_closed_query_set(self):
        calls=[]
        class C:
            autocommit=True
            info=SimpleNamespace(host=s.host.HOST,dbname='postgres',user=s.host.ADMIN,transaction_status=0,
                get_parameters=lambda:dict(sslmode='verify-full',sslrootcert=s.CA_PATH))
            pgconn=SimpleNamespace(ssl_in_use=True)
            def transaction(self):
                class T:
                    def __enter__(self):return self
                    def __exit__(self,*args):return False
                return T()
            def execute(self,q):
                calls.append(q)
                if q.startswith('SELECT session_user'):
                    return SimpleNamespace(fetchone=lambda:dict(login=s.host.ADMIN,role=s.host.ADMIN,database='postgres',readonly='on'))
                if q in s.QUERIES.values():
                    name=next(k for k,v in s.QUERIES.items() if q==v)
                    return SimpleNamespace(fetchall=lambda:fixture()[name])
                if q!='SET TRANSACTION READ ONLY':raise AssertionError('unknown query')
        self.assertIsInstance(s.read_snapshot(C()),s.CatalogSnapshot)
        self.assertEqual(calls[0],'SET TRANSACTION READ ONLY')
        self.assertEqual(set(calls[2:]),set(s.QUERIES.values()))


class QualificationTests(unittest.TestCase):
    def test_only_three_additions_and_unchanged_predecessors_accepted(self):
        before={f'predecessor-{i}':'a'*64 for i in range(828)}
        after=dict(before,**{k:'b'*64 for k in qualification.ADDITIONS})
        qualification.source_extension(before,after)
        for mode in ('changed','missing','extra','invalid_hash'):
            altered=dict(after)
            if mode=='changed':altered['predecessor-0']='c'*64
            if mode=='missing':altered.pop('predecessor-0')
            if mode=='extra':altered['unexpected']='d'*64
            if mode=='invalid_hash':altered[next(iter(qualification.ADDITIONS))]='bad'
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                qualification.source_extension(before,altered)

    def test_detached_guard_preserves_non_source_checks(self):
        import ast
        candidate=qualification.candidate
        source=candidate.controller_source((candidate.ROOT/candidate.BASE_CONTROL).read_bytes())
        function=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='guards')
        original=ast.get_source_segment(source,function)
        old="    require(p.old.prior.host.sources() == pf['sources'], 'fence_source_drift')\n    sources_valid(pf['predecessor_sources'], pf['sources'])"
        expected=original.replace(old,"    diagnostic_source_extension(pf['sources'],p.old.prior.host.sources())")
        guard=qualification.detached_guard({})
        self.assertEqual(guard.__name__,'guards')
        self.assertEqual(set(guard.__code__.co_names),set(compile(expected,'test','exec').co_consts[0].co_names))
        self.assertIn('SQL_FAILURE_SHA',expected)
        self.assertIn('checked_job',expected)
        self.assertIn('fence_audit_revocation_failure_native_drift',expected)
        self.assertNotIn('diagnostic_source_extension',original)


if __name__=='__main__':unittest.main()
