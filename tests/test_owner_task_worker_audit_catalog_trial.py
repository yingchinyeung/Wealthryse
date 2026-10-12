"""Prospective fully keyed guard; failed predecessors remain unaccepted."""
import ast
import base64
import copy
import hashlib
import lzma
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import owner_task_worker_audit_catalog_candidate as c
import deploy_owner_task_worker_audit_catalog_trial as controller
from test_owner_codex_catalog_snapshot import fixture
from test_owner_task_isolated_sql import runtime


def own_fixture():
    rows=fixture()
    for i,name in enumerate(c.ROLES):
        rows['roles'].append(dict(rows['roles'][0],oid=100+i,name=name))
    rows['databases'].append(dict(rows['databases'][0],oid=20,name=c.DATABASE,owner_oid=100))
    rows['memberships'].append(dict(rows['memberships'][0],role_oid=100,member_oid=2,grantor_oid=1))
    return rows


class AuditCatalogTrialTests(unittest.TestCase):
    def setUp(self):self.w=c.worker_module()

    def plan(self):
        n=controller.control();source={p:(ROOT/p).read_bytes() for p in n['PATHS']}
        source[c.PATH]=c.worker_source((ROOT/c.BASE_WORKER).read_bytes()).encode()
        return n,n['build'](source,{p:hashlib.sha256(v).hexdigest() for p,v in source.items()},runtime())

    def test_only_three_additions_preserve_all_838_sources(self):
        before={f'old-{i}.py':'a'*64 for i in range(838)}
        after=dict(before,**{p:'b'*64 for p in c.ADDITIONS});controller.extension(before,after)
        for changed in (dict(after,**{'old-0.py':'c'*64}),dict(after,**{'extra.py':'c'*64})):
            with self.assertRaises(ValueError):controller.extension(before,changed)

    def test_new_scope_never_restarts_failed_job_or_reuses_roles(self):
        old=c.previous.worker_module()
        self.assertNotEqual(self.w.JOB,old.JOB);self.assertNotEqual(self.w.DATABASE,old.DATABASE)
        self.assertFalse(set(self.w.ROLES)&set(old.ROLES));self.assertEqual(self.w.DDL_SHA,old.DDL_SHA)
        self.assertEqual(self.w.BASELINE_SHA,c.BASELINE_SHA)

    def test_projection_excludes_only_registered_new_scope(self):
        full=c.catalog.CatalogSnapshot.from_rows(own_fixture())
        self.assertEqual(c.project_predecessors(full).sha256,c.catalog.CatalogSnapshot.from_rows(fixture()).sha256)
        rows=own_fixture();rows['roles'].append(dict(rows['roles'][0],oid=200,name=c.previous.OWNER))
        self.assertIn(c.previous.OWNER,[r['name'] for r in c.project_predecessors(c.catalog.CatalogSnapshot.from_rows(rows)).rows()['roles']])

    def test_row_order_does_not_hide_real_permission_or_grantor_changes(self):
        before=own_fixture();shuffled=copy.deepcopy(before);shuffled['memberships'].reverse()
        original=c.project_predecessors(c.catalog.CatalogSnapshot.from_rows(before))
        self.assertEqual(original.sha256,c.project_predecessors(c.catalog.CatalogSnapshot.from_rows(shuffled)).sha256)
        for field,value in (('admin_option',False),('grantor_oid',2)):
            changed=copy.deepcopy(before);changed['memberships'][0][field]=value
            self.assertNotEqual(original.sha256,c.project_predecessors(c.catalog.CatalogSnapshot.from_rows(changed)).sha256)

    def test_new_grantor_cannot_hide_existing_principals_permission_change(self):
        rows=own_fixture();rows['memberships'].append(dict(rows['memberships'][0],grantor_oid=100))
        full=c.catalog.CatalogSnapshot.from_rows(rows)
        with self.assertRaisesRegex(ValueError,'fence_audit_catalog_grantor_escape'):c.project_predecessors(full)

    def test_failed_projection_retains_full_redacted_observation(self):
        rows=own_fixture();rows['memberships'].append(dict(rows['memberships'][0],grantor_oid=100))
        full=c.catalog.CatalogSnapshot.from_rows(rows)
        with patch.object(self.w.catalog,'read_snapshot',return_value=full):
            with self.assertRaises(ValueError):self.w.predecessor_catalog(object())
        self.assertEqual(self.w._catalog_snapshots[0]['full_sha256'],full.sha256)
        self.assertIsNone(self.w._catalog_snapshots[0]['predecessor_sha256'])

    def test_snapshot_count_is_bounded(self):
        full=c.catalog.CatalogSnapshot.from_rows(fixture())
        with patch.object(self.w.catalog,'read_snapshot',return_value=full):
            for _ in range(6):self.w.predecessor_catalog(object())
            with self.assertRaisesRegex(ValueError,'fence_audit_catalog_observation_limit'):self.w.predecessor_catalog(object())

    def test_baseline_and_final_snapshot_required_and_failure_frames_retained(self):
        source=c.worker_source((ROOT/c.BASE_WORKER).read_bytes())
        self.assertIn("'fence_audit_catalog_baseline_drift'",source)
        self.assertIn("'fence_audit_catalog_final_drift'",source)
        self.assertIn("catalog_snapshots=_catalog_snapshots",source)
        self.assertIn("historical_drift_cause_proven=False",source)
        self.assertIn("proof=revocation_proof(error,admin)",source)

    def test_new_framing_binding_rejects_missing_or_duplicate_parts(self):
        record=dict(operation_id=c.OPERATION,catalog_snapshots=[],historical_drift_cause_proven=False)
        wire=self.w.frames(record);self.assertEqual(self.w.reconstruct(wire)[0],record)
        for invalid in ([],wire+wire,[dict(wire[0],operation_id=c.previous.OPERATION)]):
            with self.assertRaises(ValueError):self.w.reconstruct(invalid)

    def test_owned_controller_retains_job_validator_and_new_marker(self):
        n=controller.control();old,guard=controller.predecessor_context()
        self.assertEqual(old['worker'].JOB,c.previous.JOB);self.assertTrue(callable(guard))
        self.assertEqual(n['JOB'],self.w.JOB_ID)
        self.assertIsNot(n['collect'].__globals__,n['checked_job'].__globals__)
        def constants(code):
            return sum([constants(x) if isinstance(x,types.CodeType) else [x] for x in code.co_consts],[])
        self.assertIn(controller.NAME_PREFIX,constants(n['dispatch'].__code__))

    def test_execution_alias_is_bound_to_new_job_not_diagnostic_job(self):
        n=controller.control();registered=dict(name='OWNERTRIAL_ADMIN_URL',secretRef=self.w.host.SECRET_NAME)
        expected={'containers':[{'env':[registered]}]};seen=[]
        alias='cappjob-'+self.w.JOB
        actual={'containers':[{'env':[dict(name=registered['name'],secretRef=alias)]}],'initContainers':[]}
        n['execution_template'](actual,expected,lambda a,b:seen.append(a))
        self.assertEqual(seen[0],expected)
        diagnostic_alias='cappjob-'+controller.metadata.prior.base.worker.JOB
        actual['containers'][0]['env'][0]['secretRef']=diagnostic_alias;seen=[]
        n['execution_template'](actual,expected,lambda a,b:seen.append(a))
        self.assertEqual(seen[0]['containers'][0]['env'][0]['secretRef'],diagnostic_alias)

    def test_single_manual_pinned_plan_and_snapshot_module(self):
        n,plan=self.plan();config=plan['definition']['properties']['configuration']
        self.assertEqual(config['replicaRetryLimit'],0)
        self.assertEqual(config['manualTriggerConfig'],dict(parallelism=1,replicaCompletionCount=1))
        self.assertEqual(config['secrets'][0]['keyVaultUrl'],n['image'].SECRET_VERSION_URL)
        self.assertEqual(plan['definition']['properties']['template']['containers'][0]['image'],n['image'].IMAGE)
        self.assertIn(('src/ibkr_etl/owner_codex_catalog_snapshot.py','ibkr_etl.owner_codex_catalog_snapshot'),n['MODULES'])

    def test_actual_derived_bundle_imports_in_fresh_process_without_sql(self):
        n,plan=self.plan();arg=plan['definition']['properties']['template']['containers'][0]['args'][3]
        node=next(x for x in ast.walk(ast.parse(arg)) if isinstance(x,ast.Call) and isinstance(x.func,ast.Attribute) and x.func.attr=='b85decode')
        script=lzma.decompress(base64.b85decode(ast.literal_eval(node.args[0].func.value).replace(':',chr(36)))).decode()
        values={x.targets[0].id:ast.literal_eval(x.value) for x in ast.parse(script).body
            if isinstance(x,ast.Assign) and isinstance(x.targets[0],ast.Name) and x.targets[0].id in ('_sources','_modules')}
        code="import sys,types\np=types.ModuleType('ibkr_etl');p.__path__=[];sys.modules['ibkr_etl']=p\n"
        code+='_sources='+repr(values['_sources'])+'\n_modules='+repr(values['_modules'])+'\n'
        code+="for path,name in _modules:\n m=types.ModuleType(name);m.__file__=path;m.__package__=name.rpartition('.')[0];sys.modules[name]=m;exec(compile(_sources[path],path,'exec'),m.__dict__)\nprint('no_sql_imported')"
        result=subprocess.run([sys.executable,'-I','-'],input=code,text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'no_sql_imported')


if __name__=='__main__':unittest.main()
