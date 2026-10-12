"""TLS observation contracts. Offline tests are not live TLS acceptance."""
import base64
import hashlib
import json
import lzma
from pathlib import Path
import subprocess
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import owner_task_isolated_tls_view_observation as w
import deploy_owner_task_isolated_tls_view_observation as c
import test_owner_task_isolated_sql as fixture


def params(user=None):
    return dict(host=w.target.HOST,user=user or w.target.LOGIN,password='SyntheticOnly',
        dbname=w.target.DATABASE,port='5432',sslmode='verify-full',sslrootcert=w.prior.CA_PATH)


def plan():
    source={k:(c.ROOT/k).read_bytes() for k in (*c.bundle.SOURCE_PATHS,w.PATH)}
    return w.build(source,{k:hashlib.sha256(v).hexdigest() for k,v in source.items()},fixture.runtime())


class Raw:
    def __init__(self,own=None,masked=None):
        self.own=own or {'ssl':True,'version':'TLSv1.3'}
        self.masked={'ssl':None,'version':None} if masked is None else masked
        self.statements=[];self.stats_count=0;self.closed=False;self.info=Mock(hostaddr='10.0.0.4')
        self.pgconn=Mock()
        self.pgconn.encrypt_password.return_value=b'SCRAM-SHA-256$4096:'+base64.b64encode(b's'*16)+b'$'+base64.b64encode(b'k'*32)+b':'+base64.b64encode(b'k'*32)
    @contextmanager
    def transaction(self):yield self
    def close(self):self.closed=True
    def execute(self,q,params=None):
        q=q if type(q)is str else q.as_string();self.statements.append((q,params));cursor=Mock()
        if 'session_user::text AS login' in q:cursor.fetchone.return_value=dict(login=w.target.LOGIN,role=w.target.LOGIN,database=w.target.DATABASE)
        elif 'pg_stat_ssl' in q:
            cursor.fetchone.return_value=self.own if self.stats_count==0 else self.masked;self.stats_count+=1
        elif 'current_user::text AS role' in q:cursor.fetchone.return_value={'role':w.target.LOGIN}
        return cursor


class TLSObservationTests(unittest.TestCase):
    def test_own_login_positive_and_capability_mask_are_separate(self):
        raw=Raw()
        with patch.object(w.prior,'_transport'):value=w.own_login_tls(raw,params())
        self.assertEqual(value['protocol'],'TLSv1.3');self.assertTrue(value['capability_statistics_masked'])
        self.assertFalse(value['monitoring_privileges_added'])
        self.assertTrue(all(q.startswith(('SELECT ','SET ')) for q,_ in raw.statements))
        self.assertTrue(any('SET LOCAL ROLE' in q for q,_ in raw.statements))
    def test_null_plaintext_old_tls_and_malformed_own_statistics_never_pass(self):
        for value in ({'ssl':None,'version':None},{'ssl':False,'version':None},{'ssl':True,'version':'TLSv1.1'},{'ssl':True,'version':'TLSv1.3','extra':1}):
            with self.subTest(value=value),patch.object(w.prior,'_transport'),self.assertRaises(ValueError):w.own_login_tls(Raw(own=value),params())
    def test_statistics_view_no_row_is_diagnostic_only(self):
        raw=Raw();raw.masked=None
        with patch.object(w.prior,'_transport'):value=w.own_login_tls(raw,params())
        self.assertEqual(value['status'],'passed');self.assertEqual(value['protocol'],'TLSv1.3')
        self.assertIsNone(value['capability_statistics']);self.assertFalse(value['capability_statistics_row_visible'])
    def test_missing_own_login_statistics_never_passes(self):
        raw=Raw();raw.own=None
        with patch.object(w.prior,'_transport'),self.assertRaises(ValueError):w.own_login_tls(raw,params())
    def test_fixed_connection_no_role_escalation_and_full_tls(self):
        connector=Mock();w.connect_exact(connector,params(),w.target.DATABASE)
        values=connector.call_args.kwargs
        self.assertNotIn('-c role=',values['options']);self.assertEqual(values['sslmode'],'verify-full')
        self.assertEqual(values['connect_timeout'],20);self.assertTrue(values['autocommit'])
        for key,value in [('host','production.invalid'),('user','other'),('sslmode','require'),('sslrootcert','other')]:
            p=params();p[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):w.connect_exact(connector,p,w.target.DATABASE)
        with self.assertRaises(ValueError):w.connect_exact(connector,params(),'postgres')
    def test_provider_text_without_sqlstate_is_not_silently_accepted(self):
        def connect(**p):raise RuntimeError('FATAL:  permission denied for database "'+p['dbname']+'"')
        probes,diagnostics=w.provider_observations(connect,params())
        self.assertEqual(len(probes),2);self.assertTrue(all(p['outcome']=='unclassified_failure' for p in probes))
        self.assertTrue(all(d['classification']=='exact_database_permission_denied_text' for d in diagnostics))
        with self.assertRaises(w.prior.boundary.BoundaryError):w.prior.boundary.validate_provider_behavior(probes)
    def test_provider_connected_remains_object_review_pending(self):
        def connect(**p):
            raw=Mock();raw.execute.return_value.fetchone.return_value=dict(login=w.target.LOGIN,database=p['dbname']);return raw
        with patch.object(w.prior,'_transport'):probes,diagnostics=w.provider_observations(connect,params())
        self.assertTrue(all(p['outcome']=='connected_requires_object_review' for p in probes))
        self.assertTrue(all(d['classification']=='connected_no_object_values_read' for d in diagnostics))
        with self.assertRaises(w.prior.boundary.BoundaryError):w.prior.boundary.validate_provider_behavior(probes)
    def test_fixture_drift_precedes_rotation_and_passed_crud_not_replayed(self):
        raw=Raw()
        with patch.object(w.prior.parser,'validate_admin_secret',return_value=params(w.target.ADMIN)),patch.object(w.prior.parser,'require_ca_bundle'),patch.object(w.prior,'_transport'),patch.object(w,'fixture_fingerprint',side_effect=ValueError('isolated_sql_post_trial_authority_changed')):
            result=w.run('SyntheticAdminSecret',connect=lambda **p:raw)
        self.assertEqual(result['credential_rotations'],0);self.assertFalse(result['restricted_crud_repeated'])
        self.assertFalse(any(q.startswith('ALTER ') for q,_ in raw.statements));self.assertTrue(raw.closed)
    def test_success_preserves_fixture_and_emits_no_credentials(self):
        raw=Raw();connector=Mock(return_value=raw);state={'permission_manifest_sha256':'a'*64,'registered_scope_state_sha256':'9aa54f84f2b59dd1c18fb9535bf81d7249df13f6dc4a589436df3940460897e6'}
        with patch.object(w.prior.parser,'validate_admin_secret',return_value=params(w.target.ADMIN)),patch.object(w.prior.parser,'require_ca_bundle'),patch.object(w.prior,'_transport'),patch.object(w,'fixture_fingerprint',return_value=state),patch.object(w.preparation,'scope_metadata'),patch.object(w.preparation,'validate_registered_metadata'),patch.object(w,'own_login_tls',return_value={'status':'passed'}),patch.object(w.prior,'_hostname_probe',return_value={'status':'passed'}),patch.object(w,'provider_observations',return_value=([],[])):
            value=w.run('SyntheticAdminSecret',connect=connector)
        self.assertEqual(value['status'],'tls_observation_completed_additional_acceptance_pending');self.assertEqual(value['credential_rotations'],1)
        self.assertTrue(value['fixture_unchanged']);self.assertFalse(value['step_2_complete'])
        self.assertEqual(sum(q.startswith('ALTER ROLE') for q,_ in raw.statements),1)
        self.assertNotIn('SyntheticAdminSecret',w.canonical(value).decode());self.assertNotIn('SyntheticOnly',w.canonical(value).decode())
    def test_full_bundle_fixed_target_lossless_no_argument_expansion(self):
        p=plan();d=p['definition'];args=d['properties']['template']['containers'][0]['args']
        self.assertLessEqual(len(w.canonical(args)),65536);self.assertNotIn('$',w.canonical(args).decode())
        packed=args[3].split("b85decode('",1)[1].split("'.replace",1)[0]
        source=lzma.decompress(base64.b85decode(packed.replace(':',chr(36))))
        self.assertEqual(hashlib.sha256(source).hexdigest(),d['tags']['fullSourceSha256'])
        self.assertEqual(len(p['source_hashes']),15);self.assertEqual(d['tags']['operation'],w.OPERATION)
        self.assertEqual(d['properties']['configuration']['replicaRetryLimit'],0)
        self.assertFalse(p['step_2_complete'])
    def test_bad_runtime_rejected_before_secret_when_serialized_bundle_executes(self):
        p=plan();args=p['definition']['properties']['template']['containers'][0]['args'];packed=args[3].split("b85decode('",1)[1].split("'.replace",1)[0]
        source=lzma.decompress(base64.b85decode(packed.replace(':',chr(36)))).decode()
        bad=source.replace("_runtime=", "_runtime=",1).replace(fixture.runtime()['ca_sha256'],'0'*64)
        # Environment/source gates also fail closed on this Windows test host.
        result=subprocess.run([sys.executable,'-I','-B','-'],input=bad,text=True,capture_output=True,timeout=20)
        self.assertNotEqual(result.returncode,0);self.assertIn('OWNER_TASK_ISOLATED_TLS_VIEW_OBSERVATION_LAUNCH_FAILURE_V1',result.stdout)
        self.assertNotIn(w.PREFIX,result.stdout);self.assertNotIn('SyntheticOnly',result.stdout+result.stderr)
    def test_consumed_start_rejected_before_receipt_or_network(self):
        with patch.object(Path,'exists',return_value=True),patch.object(c,'load') as load:
            with self.assertRaises(ValueError):c.dispatch(Mock(),'start')
        load.assert_not_called()
    def test_all_786_frozen_hashes_unchanged_exactly_three_additions(self):
        before={f'old/{i}.py':'a'*64 for i in range(786)}
        after=dict(before,**{p:'b'*64 for p in c.ADDITIONS})
        c.sources_valid(before,after);self.assertEqual(len(after),789)
        after['old/0.py']='c'*64
        with self.assertRaises(ValueError):c.sources_valid(before,after)
    def test_previous_job_restarts_and_unregistered_routes_are_denied(self):
        with patch.object(c,'AzureCliCredential') as credential:
            transport=c.Transport()
            for method,path in [('POST',c.safe_control.JOB+'/start'),('POST',c.resume_control.JOB+'/start'),('DELETE',c.JOB),('GET',c.JOB+'/secrets')]:
                with self.subTest(path=path),self.assertRaises(ValueError):transport.call(method,path)
            credential.return_value.get_token.assert_not_called()


if __name__=='__main__':unittest.main()


