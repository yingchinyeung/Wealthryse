"""Read-only session diagnosis contracts; fixtures do not prove live behavior."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import owner_task_isolated_scope_diagnostic as d
import owner_task_isolated_sql_root_launch as launch
import test_owner_task_isolated_sql as fixture
import deploy_owner_task_scope_diagnostic as control


class ScopeDiagnosticTests(unittest.TestCase):
    def identity(self,path):
        return dict(database=d.target.DATABASE,role=d.target.SCHEMA_OWNER,login=d.target.ADMIN,
            search_path=path,effective_schemas=[d.target.SCHEMA,'pg_catalog'],
            tenant_scope=None,owner_scope=None,temp_schema=0)

    def test_exact_scope_and_admin_not_fresh_login(self):
        for path in (d.OLD_PATH,d.EXACT_PATH): d.validate_identity(self.identity(path),path)
        for field,value in [('database','postgres'),('role',d.target.ADMIN),('login',d.target.LOGIN),
            ('effective_schemas',['public','pg_catalog']),('tenant_scope','x'),('owner_scope','x'),
            ('temp_schema',1),('temp_schema',False),('search_path',d.OLD_PATH)]:
            row=self.identity(d.EXACT_PATH); row[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                d.validate_identity(row,d.EXACT_PATH)

    def test_worker_has_only_settings_and_select_no_installer_or_model_calls(self):
        raw=Mock(); paths=[]
        def execute(q,p=None):
            self.assertTrue(q.startswith(('SET ','SELECT ')))
            if 'set_config' in q: paths.append(p[0])
            cursor=Mock(); cursor.fetchone.return_value=self.identity(paths[-1]) if q==d.IDENTITY_SQL else None
            return cursor
        raw.execute.side_effect=execute
        with patch.object(d.prior_worker.parser,'validate_admin_secret',return_value={}), \
             patch.object(d.prior_worker.parser,'require_ca_bundle'), \
             patch.object(d.prior_worker,'_connect',return_value=raw), \
             patch.object(d.prior_worker,'_transport'), \
             patch.object(d.preparation,'bind_trusted_connection'), \
             patch.object(d.preparation,'scope_metadata',return_value={}), \
             patch.object(d.preparation,'validate_registered_metadata'), \
             patch.object(d.permissions,'verify_permissions',return_value={'status':'passed'}), \
             patch.object(d.preparation,'prepare_trial',side_effect=AssertionError('no setup')):
            result=d.run('synthetic-secret-never-retained',connect=lambda:None)
        self.assertEqual(result['status'],'readonly_scope_diagnostic_completed')
        self.assertEqual(paths,[d.OLD_PATH,d.EXACT_PATH]); raw.close.assert_called_once()
        for key in ('fresh_restricted_login_verified','model_credential_isolation_verified','step_2_complete'):
            self.assertIs(result[key],False)
        for key in ('source_rows_written','credential_rotations','data_changes','model_calls'):
            self.assertEqual(result[key],0)
        self.assertNotIn('synthetic-secret',json.dumps(result))

    def test_provider_error_text_not_reflected_and_connection_closed(self):
        with patch.object(d.prior_worker.parser,'validate_admin_secret',side_effect=RuntimeError('secret/password')):
            result=d.run('credential',connect=lambda:None)
        self.assertEqual(result['reason'],'scope_diagnostic_unaccepted')
        self.assertNotIn('password',json.dumps(result))

    def test_artifact_new_name_exact_image_grants_and_guard_preserved(self):
        root=Path(__file__).resolve().parents[1]
        runtime=fixture.runtime()
        source={k:(root/k).read_bytes() for k in (*launch.SOURCE_PATHS,d.PATH)}
        hashes={k:hashlib.sha256(v).hexdigest() for k,v in source.items()}
        before=launch.LAUNCHER; plan=d.build(source,hashes,runtime)
        self.assertEqual(launch.LAUNCHER,before)
        body=plan['definition']; self.assertEqual(body['tags']['operation'],d.OPERATION)
        self.assertNotEqual(d.JOB,launch.worker.JOB); self.assertLessEqual(len(d.JOB),32)
        old=launch.build({k:source[k] for k in launch.SOURCE_PATHS},
            {k:hashes[k] for k in launch.SOURCE_PATHS},runtime)['definition']
        self.assertEqual(body['identity'],old['identity'])
        self.assertEqual(body['properties']['configuration'],old['properties']['configuration'])
        self.assertEqual(body['properties']['template']['containers'][0]['image'],launch.prior.IMAGE)
        for mutation in ({**hashes,d.PATH:'0'*64},{k:v for k,v in hashes.items() if k!=d.PATH}):
            with self.assertRaises(ValueError): d.build(source,mutation,runtime)

    def test_exact_additive_source_guard_no_predecessor_edits(self):
        before={f'prior/{i}.py':'a'*64 for i in range(774)}
        after=dict(before,**{k:'b'*64 for k in control.ADDITIONS})
        control.sources_valid(before,after)
        self.assertEqual(len(after),777)
        after['prior/0.py']='c'*64
        with self.assertRaises(ValueError):control.sources_valid(before,after)

    def test_fixed_routes_reject_credential_fetch_mutations_and_old_restart(self):
        with patch.object(control,'AzureCliCredential') as credential:
            client=control.Transport(); credential.return_value.get_token.side_effect=AssertionError('no token')
            for method,path in [('GET',control.JOB+'/secrets'),('GET',control.parent.JOB),
                ('GET',control.JOB+'/executions/wrong'),('POST',control.parent.JOB+'/start'),
                ('DELETE',control.JOB)]:
                with self.subTest(method=method,path=path),self.assertRaises(ValueError):
                    client.call(method,path)
            credential.return_value.get_token.assert_not_called()

    def test_consumed_dispatch_is_inspect_only_before_load(self):
        with patch.object(Path,'exists',return_value=True),patch.object(control,'load') as load:
            with self.assertRaises(ValueError):control.dispatch(Mock(),'start')
            load.assert_not_called()

    def test_native_readback_allows_only_reviewed_empty_descriptors(self):
        import copy
        root=Path(__file__).resolve().parents[1]
        source={k:(root/k).read_bytes() for k in (*launch.SOURCE_PATHS,d.PATH)}
        hashes={k:hashlib.sha256(v).hexdigest() for k,v in source.items()}
        plan=d.build(source,hashes,fixture.runtime())
        identity=dict(principalId='11111111-1111-4111-8111-111111111111',
            clientId='22222222-2222-4222-8222-222222222222')
        native=copy.deepcopy(plan['definition']); native.update(id=control.JOB,name=d.JOB,type='Microsoft.App/jobs')
        native['identity']['userAssignedIdentities'][control.MI]=identity
        native['properties']['provisioningState']='Succeeded'
        native['properties']['template'].update(initContainers=None,volumes=None)
        native['properties']['template']['containers'][0]['resources']['ephemeralStorage']=''
        before=copy.deepcopy(native); control.validate_job(native,plan,identity); self.assertEqual(native,before)
        for kind in ('old_name','cpu','mount','env'):
            bad=copy.deepcopy(native)
            if kind=='old_name':bad['name']=control.parent.worker.JOB
            elif kind=='cpu':bad['properties']['template']['containers'][0]['resources']['cpu']=1
            elif kind=='mount':bad['properties']['template']['volumes']=[]
            else:bad['properties']['template']['containers'][0]['env'].append({'name':'PGPASSWORD','value':'synthetic'})
            with self.subTest(kind=kind),self.assertRaises(ValueError):control.validate_job(bad,plan,identity)

    def test_real_bundle_imports_reject_runtime_before_any_credential_read(self):
        import subprocess
        root=Path(__file__).resolve().parents[1]
        source={k:(root/k).read_bytes() for k in (*launch.SOURCE_PATHS,d.PATH)}
        hashes={k:hashlib.sha256(v).hexdigest() for k,v in source.items()}
        runtime=fixture.runtime()
        # Guaranteed descriptor mismatch on every OS, including the exact
        # Linux image: this is a pre-credential rejection test, not a skip.
        runtime['ca_sha256']='0'*64
        plan=d.build(source,hashes,runtime)
        import os
        env={k:v for k,v in os.environ.items() if not k.upper().startswith('PG')
            and k.upper() not in ('SSL_CERT_FILE','SSL_CERT_DIR','OPENSSL_CONF','OPENSSL_MODULES','IBKR_PROJECT_ROOT')}
        env['IBKR_PROJECT_ROOT']='/app'
        args=plan['definition']['properties']['template']['containers'][0]['args']
        cmd=[sys.executable,'-I','-B','-c',"import sys;exec(compile(sys.stdin.read(),'bounded-diagnostic-fixture','exec'))"]
        observed=subprocess.run(cmd,input=args[-1],capture_output=True,text=True,env=env,timeout=30)
        self.assertEqual(observed.returncode,1)
        self.assertEqual(observed.stderr,'')
        line=observed.stdout.strip()
        self.assertTrue(line.startswith('OWNER_TASK_ISOLATED_SCOPE_LAUNCH_FAILURE_V1 '))
        result=json.loads(line.split(' ',1)[1])
        self.assertEqual(result['stage'],'runtime_validation')
        self.assertIs(result['sql_worker_entered'],False)


if __name__=='__main__': unittest.main()
