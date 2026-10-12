"""Prepared-fixture continuation guards; mocks are not live SQL acceptance."""
import base64
import copy
import hashlib
import json
import lzma
import os
from pathlib import Path
import subprocess
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import owner_task_isolated_sql_safe_encoding as w
import deploy_owner_task_isolated_sql_safe_encoding as c
import test_owner_task_isolated_sql as fixture


def plan(runtime=None):
    root=Path(__file__).resolve().parents[1]
    paths=(*c.bundle.SOURCE_PATHS,w.diagnosis.PATH,w.PATH)
    source={k:(root/k).read_bytes() for k in paths}
    hashes={k:hashlib.sha256(v).hexdigest() for k,v in source.items()}
    return w.build(source,hashes,runtime or fixture.runtime())


class RawFixture:
    def __init__(self,*,altered_authority=False):
        self.statements=[];self.altered_authority=altered_authority
        self.info=Mock(hostaddr='10.0.0.4')
        self.pgconn=Mock()
        salt=base64.b64encode(b's'*16); key=base64.b64encode(b'k'*32)
        self.pgconn.encrypt_password.return_value=b'SCRAM-SHA-256$4096:'+salt+b'$'+key+b':'+key
    @contextmanager
    def transaction(self):yield self
    def close(self):pass
    def execute(self,query,params=None):
        text=query if type(query)is str else query.as_string()
        self.statements.append((text,params));cursor=Mock();p=w.preparation
        if '.tenants ' in text:
            cursor.fetchall.return_value=[dict(tenant_id=p.TENANT_A),dict(tenant_id=p.TENANT_B)]
        elif '.owner_task_authority_fixture ' in text:
            cursor.fetchall.return_value=[dict(tenant_id=t,owner_object_id=o,
                active=not self.altered_authority,role_name='owner',registered_subject_ids=['SYNTH_A','SYNTH_B'],
                authorized_subject_ids=['SYNTH_A','SYNTH_B']) for t,o in
                ((p.TENANT_A,p.OWNER_A),(p.TENANT_A,p.OWNER_B),(p.TENANT_B,p.OWNER_A))]
        elif '.owner_task_worker_fixture ' in text:
            cursor.fetchall.return_value=[dict(tenant_id=t,worker_id=p.WORKER,
                service_principal_id=p.PRINCIPAL,enabled=True) for t in (p.TENANT_A,p.TENANT_B)]
        cursor.fetchone.return_value={'ssl':True,'version':'TLSv1.3'} if 'pg_stat_ssl' in text else {'count':0}
        return cursor


class SafeEncodingTests(unittest.TestCase):
    def params(self):return dict(host=w.target.HOST,user=w.target.LOGIN,password='SyntheticOnly',
        dbname=w.target.DATABASE,port='5432',sslmode='verify-full',sslrootcert=w.prior.CA_PATH)

    def test_startup_space_escaped_exact_roles_tls_and_timeouts_preserved(self):
        connect=Mock();w.connect_exact(connect,self.params(),w.target.DATABASE,restricted=True)
        actual=connect.call_args.kwargs
        self.assertIn('-c role='+w.target.CAPABILITY,actual['options'])
        self.assertIn('-c search_path='+w.target.SCHEMA+',\\ pg_catalog',actual['options'])
        self.assertEqual(actual['sslmode'],'verify-full');self.assertEqual(actual['sslrootcert'],w.prior.CA_PATH)
        self.assertEqual(actual['connect_timeout'],20);self.assertIs(actual['autocommit'],True)
        self.assertIn('statement_timeout=45000',actual['options'])
        for db in sorted(w.prior.boundary.PROVIDER_DATABASES):
            w.connect_exact(connect,self.params(),db,restricted=True)
            self.assertNotIn('-c role=',connect.call_args.kwargs['options'])

    def test_wrong_host_user_database_or_tls_refused_before_connector(self):
        connect=Mock()
        for key,value in [('host','production.example'),('user','unknown'),('sslmode','require'),('sslrootcert','x')]:
            params=self.params();params[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):w.connect_exact(connect,params,w.target.DATABASE)
        with self.assertRaises(ValueError):w.connect_exact(connect,self.params(),'production')
        connect.assert_not_called()

    def fixture_patches(self):
        return (patch.object(w.preparation,'bind_trusted_connection'),
            patch.object(w.preparation,'validate_registered_metadata'),
            patch.object(w.preparation,'scope_metadata',return_value={}),
            patch.object(w.permissions,'verify_permissions',return_value={'status':'passed'}))

    def test_fixture_review_is_read_only_and_known_scopes_not_all_rows_claim(self):
        raw=RawFixture();a,b,d,e=self.fixture_patches()
        with a,b,d,e:result=w.verify_fixture(raw)
        self.assertEqual(result['registered_owner_scopes_verified'],3)
        self.assertIs(result['all_unknown_owner_scopes_claimed_empty'],False)
        self.assertTrue(all(q.startswith(('SELECT ','SET ')) for q,_ in raw.statements))
        self.assertEqual(sum('count(*)' in q for q,_ in raw.statements),9)
        raw=RawFixture(altered_authority=True);a,b,d,e=self.fixture_patches()
        with a,b,d,e,self.assertRaises(ValueError):w.verify_fixture(raw)
        self.assertFalse(any(q.startswith('ALTER ') for q,_ in raw.statements))

    def test_failed_fixture_review_never_rotates_password_or_calls_harness(self):
        raw=RawFixture();a,b,d,e=self.fixture_patches()
        with a,b,d,e,patch.object(w.prior.parser,'validate_admin_secret',return_value=self.params()), \
             patch.object(w.prior.parser,'require_ca_bundle'),patch.object(w.prior,'_transport'), \
             patch.object(w.diagnosis,'run',return_value={'status':'readonly_scope_diagnostic_completed'}), \
             patch.object(w,'verify_fixture',side_effect=ValueError('resume_fixture_authority_changed')), \
             patch.object(w.prior.trial,'verify_trial') as trial:
            result=w.run('synthetic-admin-only',connect=Mock(return_value=raw))
        self.assertEqual(result['credential_rotations'],0);trial.assert_not_called()
        self.assertFalse(any(q.startswith('ALTER ') for q,_ in raw.statements))

    def test_one_rotation_after_review_no_setup_and_no_credential_in_result(self):
        raw=RawFixture();a,b,d,e=self.fixture_patches();password='SyntheticCredential_'+'A'*48
        with a,b,d,e,patch.object(w.prior.parser,'validate_admin_secret',return_value=self.params()), \
             patch.object(w.prior.parser,'require_ca_bundle'),patch.object(w.prior,'_transport'), \
             patch.object(w.diagnosis,'run',return_value={'status':'readonly_scope_diagnostic_completed'}), \
             patch.object(w.secrets,'token_urlsafe',return_value=password), \
             patch.object(w.prior.trial,'verify_trial',return_value={'status':'passed','checks':list(w.prior.trial.CHECKS)}), \
             patch.object(w.prior,'_hostname_probe',return_value={'status':'passed'}), \
             patch.object(w.prior,'_provider_probes',return_value=[]), \
             patch.object(w.preparation,'prepare_trial',side_effect=AssertionError('no setup')):
            result=w.run('synthetic-admin-only',connect=Mock(return_value=raw))
        self.assertEqual(result['credential_rotations'],1)
        alters=[q for q,_ in raw.statements if q.startswith('ALTER ')]
        self.assertEqual(len(alters),1);self.assertIn(w.target.LOGIN,alters[0])
        self.assertIn('SCRAM-SHA-256',alters[0]);self.assertNotIn(password,alters[0])
        self.assertNotIn(password,json.dumps(result));self.assertNotIn('synthetic-admin-only',json.dumps(result))
        self.assertNotIn('SCRAM-SHA-256',json.dumps(result))
        self.assertIs(result['fresh_restricted_login_verified'],True)
        self.assertIs(result['step_2_complete'],False);self.assertIs(result['model_credential_isolation_verified'],False)
        self.assertEqual(result['provider_boundary']['status'],'unaccepted_requires_behavior_review')

    def test_exact_780_plus_three_sources_and_consumed_stage_refusal(self):
        before={f'old/{i}.py':'a'*64 for i in range(780)}
        after=dict(before,**{p:'b'*64 for p in c.ADDITIONS});c.sources_valid(before,after)
        self.assertEqual(len(after),783);after['old/1.py']='c'*64
        with self.assertRaises(ValueError):c.sources_valid(before,after)
        with patch.object(Path,'exists',return_value=True),patch.object(c,'load') as load:
            with self.assertRaises(ValueError):c.dispatch(Mock(),'start')
            load.assert_not_called()

    def test_new_physical_artifact_complete_base85_bundle_inside_original_bound(self):
        p=plan();self.assertEqual(p,plan());self.assertEqual(len(p['source_hashes']),16)
        body=p['definition'];args=body['properties']['template']['containers'][0]['args']
        self.assertLessEqual(len(w.canonical(args)),65536)
        encoded=args[-1].split("b85decode('",1)[1].split("'.replace",1)[0]
        source=lzma.decompress(base64.b85decode(encoded.replace(':',chr(36)))).decode();compile(source,'resume-fixture','exec')
        self.assertEqual(hashlib.sha256(source.encode()).hexdigest(),body['tags']['fullSourceSha256'])
        self.assertIn('owner_task_isolated_sql_safe_encoding',source)
        self.assertLess(source.index('_stage="runtime_validation"'),source.index('os.environ.pop("OWNERTRIAL_ADMIN_URL"'))
        self.assertNotEqual(c.JOB,c.parent.JOB);self.assertNotEqual(c.JOB,c.diagnostic_control.JOB)
        self.assertEqual(body['properties']['configuration']['replicaRetryLimit'],0)
        self.assertEqual(body['identity']['userAssignedIdentities'],{c.MI:{}})

    def test_native_definition_readback_preserves_input_rejects_old_name_and_env(self):
        p=plan();identity=dict(principalId='11111111-1111-4111-8111-111111111111',clientId='22222222-2222-4222-8222-222222222222')
        v=copy.deepcopy(p['definition']);v.update(id=c.JOB,name=w.JOB,type='Microsoft.App/jobs')
        v['identity']['userAssignedIdentities'][c.MI]=identity;v['properties']['provisioningState']='Succeeded'
        v['properties']['template'].update(initContainers=None,volumes=None)
        v['properties']['template']['containers'][0]['resources']['ephemeralStorage']=''
        before=copy.deepcopy(v);c.validate_job(v,p,identity);self.assertEqual(v,before)
        bad=copy.deepcopy(v);bad['name']=w.diagnosis.JOB
        with self.assertRaises(ValueError):c.validate_job(bad,p,identity)
        bad=copy.deepcopy(v);bad['properties']['template']['containers'][0]['env'].append({'name':'PGPASSWORD','value':'synthetic'})
        with self.assertRaises(ValueError):c.validate_job(bad,p,identity)

    def test_unknown_routes_and_old_restarts_rejected_before_credentials(self):
        with patch.object(c,'AzureCliCredential') as credential:
            client=c.Transport()
            for method,path in [('POST',c.parent.JOB+'/start'),('POST',c.diagnostic_control.JOB+'/start'),('POST',c.resume_control.JOB+'/start'),
                ('GET',c.JOB+'/secrets'),('DELETE',c.JOB),('GET',c.JOB+'/executions/unknown')]:
                with self.subTest(path=path),self.assertRaises(ValueError):client.call(method,path)
            credential.return_value.get_token.assert_not_called()

    def test_real_bundle_import_runtime_rejection_before_credential_acquisition(self):
        runtime=fixture.runtime();runtime['ca_sha256']='0'*64;p=plan(runtime)
        args=p['definition']['properties']['template']['containers'][0]['args']
        env={k:v for k,v in os.environ.items() if not k.upper().startswith('PG') and k.upper() not in
            ('SSL_CERT_FILE','SSL_CERT_DIR','OPENSSL_CONF','OPENSSL_MODULES','IBKR_PROJECT_ROOT','OWNERTRIAL_ADMIN_URL')}
        env['IBKR_PROJECT_ROOT']='/app'
        observed=subprocess.run([sys.executable,'-I','-B','-c',"import sys;exec(compile(sys.stdin.read(),'resume-import-fixture','exec'))"],
            input=args[-1],capture_output=True,text=True,env=env,timeout=30)
        self.assertEqual(observed.returncode,1);self.assertEqual(observed.stderr,'')
        self.assertTrue(observed.stdout.startswith('OWNER_TASK_ISOLATED_SQL_SAFE_ENCODING_LAUNCH_FAILURE_V1 '))
        result=json.loads(observed.stdout.split(' ',1)[1]);self.assertEqual(result['stage'],'runtime_validation')
        self.assertIs(result['sql_worker_entered'],False)


    def test_argv_has_no_expansion_metacharacter_and_codec_is_lossless(self):
        p=plan();args=p['definition']['properties']['template']['containers'][0]['args']
        self.assertNotIn(chr(36),w.canonical(args).decode())
        self.assertEqual(args,[a.replace(chr(36)*2,chr(36)) for a in args])
        packed=args[-1].split("b85decode('",1)[1].split("'.replace",1)[0]
        self.assertNotIn(chr(36),packed)
        decoded=lzma.decompress(base64.b85decode(packed.replace(':',chr(36))))
        self.assertEqual(hashlib.sha256(decoded).hexdigest(),p['definition']['tags']['fullSourceSha256'])

    def test_frozen_predecessor_codec_corrupts_under_argument_expansion(self):
        import owner_task_isolated_sql_resume as old
        root=Path(__file__).resolve().parents[1]
        paths=(*c.bundle.SOURCE_PATHS,old.diagnosis.PATH,old.PATH)
        source={k:(root/k).read_bytes() for k in paths}
        p=old.build(source,{k:hashlib.sha256(v).hexdigest() for k,v in source.items()},fixture.runtime())
        argument=p['definition']['properties']['template']['containers'][0]['args'][-1]
        packed=argument.split("b85decode('",1)[1].split("')",1)[0]
        self.assertIn(chr(36)*2,packed)
        original=lzma.decompress(base64.b85decode(packed))
        self.assertEqual(hashlib.sha256(original).hexdigest(),p['definition']['tags']['fullSourceSha256'])
        with self.assertRaises((ValueError,lzma.LZMAError)):
            lzma.decompress(base64.b85decode(packed.replace(chr(36)*2,chr(36))))


if __name__=='__main__':unittest.main()

