import base64,copy,errno,hashlib,io,json,lzma,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import owner_task_model_isolation_trial as w
import deploy_owner_task_model_isolation_trial as c

CLIENT='11111111-1111-1111-1111-111111111111'
PRINCIPAL='22222222-2222-2222-2222-222222222222'
REGISTERED=dict(clientId=CLIENT,principalId=PRINCIPAL,tenantId=w.TENANT)

class ModelIsolationTests(unittest.TestCase):
 def environment(self):return dict(AZURE_CLIENT_ID=CLIENT,IDENTITY_ENDPOINT='http://localhost:42356/msi/token',IDENTITY_HEADER='synthetic-header')
 def plan(self):
  raw=(c.ROOT/w.PATH).read_bytes();return c.build(raw,hashlib.sha256(raw).hexdigest(),REGISTERED)
 def job(self):
  plan=self.plan();value=copy.deepcopy(plan['definition']);value.update(id=c.JOB,name=w.JOB,type='Microsoft.App/jobs')
  value['identity']['userAssignedIdentities'][w.MI]={k:REGISTERED[k] for k in ('principalId','clientId')}
  value['properties']['provisioningState']='Succeeded';return value,plan
 def test_sources_preserve_795(self):
  before={str(i):'a'*64 for i in range(795)};c.sources_valid(before,dict(before,**{k:'b'*64 for k in c.ADDITIONS}))
 def test_old_source_drift_rejected(self):
  before={str(i):'a'*64 for i in range(795)};after=dict(before,**{k:'b'*64 for k in c.ADDITIONS});after['0']='c'*64
  with self.assertRaises(w.IsolationError):c.sources_valid(before,after)
 def test_unknown_addition_rejected(self):
  before={str(i):'a'*64 for i in range(795)}
  with self.assertRaises(w.IsolationError):c.sources_valid(before,dict(before,**{k:'b'*64 for k in c.ADDITIONS},unknown='c'*64))
 def test_canonical_rejects_nan(self):
  with self.assertRaises(ValueError):w.canonical({'x':float('nan')})
 def test_error_never_echoes_secrets(self):self.assertEqual(str(w.IsolationError('Bearer secret')),'model_isolation_unaccepted')
 def test_uuid_exact(self):
  for value in (None,True,CLIENT.upper().replace('1','A',1),CLIENT+'\n'):
   with self.assertRaises(w.IsolationError):w.uuid_value(value)
 def test_identity_loopback_canonical(self):
  r=w.identity_request(self.environment(),CLIENT,'https://ai.azure.com');self.assertTrue(r.full_url.startswith('http://127.0.0.1:42356/msi/token?'));self.assertNotIn('synthetic-header',r.full_url)
 def test_identity_external_or_ambiguous_denied(self):
  for value in ('https://localhost:42356/msi/token','http://example.com:42356/msi/token','http://localhost:42356/msi/token?a=b','http://user@localhost:42356/msi/token','http://localhost:42356/msi/token#x','http://localhost:bad/msi/token'):
   with self.subTest(value=value),self.assertRaises(w.IsolationError):w.identity_request(dict(self.environment(),IDENTITY_ENDPOINT=value),CLIENT,'https://ai.azure.com')
 def test_identity_unknown_audience_denied(self):
  with self.assertRaises(w.IsolationError):w.identity_request(self.environment(),CLIENT,'https://graph.microsoft.com')
 def test_identity_wrong_client_denied(self):
  with self.assertRaises(w.IsolationError):w.identity_request(dict(self.environment(),AZURE_CLIENT_ID=PRINCIPAL),CLIENT,'https://ai.azure.com')
 def test_header_whitespace_denied(self):
  with self.assertRaises(w.IsolationError):w.identity_request(dict(self.environment(),IDENTITY_HEADER='bad\nheader'),CLIENT,'https://ai.azure.com')
 def test_token_claim_binding(self):
  claims=dict(aud='https://ai.azure.com',tid=w.TENANT,oid=PRINCIPAL,appid=CLIENT,exp=9999999999)
  bearer='header.'+base64.urlsafe_b64encode(w.canonical(claims)).decode().rstrip('=')+'.signature'
  reply=Mock();reply.__enter__=Mock(return_value=reply);reply.__exit__=Mock();reply.status=200;reply.read.return_value=w.canonical(dict(access_token=bearer))
  with patch.object(w,'build_opener') as opener:
   opener.return_value.open.return_value=reply;self.assertEqual(w.token(self.environment(),CLIENT,PRINCIPAL,'https://ai.azure.com'),bearer)
  claims['oid']=CLIENT;reply.read.return_value=w.canonical(dict(access_token='header.'+base64.urlsafe_b64encode(w.canonical(claims)).decode().rstrip('=')+'.signature'))
  with patch.object(w,'build_opener') as opener,self.assertRaises(w.IsolationError):
   opener.return_value.open.return_value=reply;w.token(self.environment(),CLIENT,PRINCIPAL,'https://ai.azure.com')
 def deny(self,kind,code=403,body=None):
  url=w.SECRET_URL if kind=='secret' else w.ARM_URL
  error=HTTPError(url,code,'synthetic',{},io.BytesIO(w.canonical(body or {'error':{'code':'Forbidden','innererror':{'code':'ForbiddenByRbac'}}})))
  with patch.object(w,'build_opener') as opener:
   opener.return_value.open.side_effect=error;return w.denial(url,'synthetic-not-token',kind)
 def test_secret_real_rbac_denial(self):self.assertTrue(self.deny('secret')['effective_service_denial_verified'])
 def test_server_real_rbac_denial(self):self.assertTrue(self.deny('server',body={'error':{'code':'AuthorizationFailed'}})['effective_service_denial_verified'])
 def test_firewall_not_permission_pass(self):
  with self.assertRaises(w.IsolationError):self.deny('secret',body={'error':{'code':'Forbidden','innererror':{'code':'ForbiddenByFirewall'}}})
 def test_unauthorized_not_permission_pass(self):
  with self.assertRaises(w.IsolationError):self.deny('secret',code=401)
 def test_successful_secret_body_never_read(self):
  reply=Mock();reply.__enter__=Mock(return_value=reply);reply.__exit__=Mock(return_value=False)
  with patch.object(w,'build_opener') as opener,self.assertRaises(w.IsolationError):
   opener.return_value.open.return_value=reply;w.denial(w.SECRET_URL,'synthetic','secret')
  reply.read.assert_not_called()
 def test_unregistered_secret_precedes_request(self):
  with patch.object(w,'build_opener') as opener,self.assertRaises(w.IsolationError):w.denial(w.SECRET_URL+'x','synthetic','secret')
  opener.assert_not_called()
 def companion(self):return dict(uid=10001,gid=10001,probes={k:dict(denied=True,errno=errno.EPERM) for k in ('parent_environ','parent_mem','parent_fds','parent_root','parent_synthetic_memory')})
 def test_all_same_uid_probes_required(self):self.assertTrue(w.validate_companion(self.companion())['same_uid_adversarial_checks_verified'])
 def test_successful_probe_not_pass(self):
  v=self.companion();v['probes']['parent_mem']['denied']=False
  with self.assertRaises(w.IsolationError):w.validate_companion(v)
 def test_missing_probe_not_pass(self):
  v=self.companion();v['probes'].pop('parent_root')
  with self.assertRaises(w.IsolationError):w.validate_companion(v)
 def test_missing_parent_not_pass(self):
  v=self.companion();v['probes']['parent_mem']['errno']=errno.ENOENT
  with self.assertRaises(w.IsolationError):w.validate_companion(v)
 def test_companion_compiles_and_uses_synthetic_address(self):
  compile(w.COMPANION,'companion','exec');self.assertNotIn('TOKEN',w.COMPANION);self.assertIn('remote=IOV(address,1)',w.COMPANION)
 def test_nonlinux_cannot_claim_live_protection(self):
  with patch.object(w.sys,'platform','win32'),self.assertRaises(w.IsolationError):w.harden_parent()
 def test_packed_source_exact_dollar_free(self):
  p=self.plan();args=p['definition']['properties']['template']['containers'][0]['args'];self.assertEqual(args[:3],['-I','-B','-c']);self.assertNotIn('$',args[-1]);self.assertLessEqual(len(w.canonical(args)),65536)
  text=lzma.decompress(base64.b64decode(args[-1].split("b64decode('")[1].split("'))")[0])).decode();compile(text,'launch','exec');self.assertIn(repr((c.ROOT/w.PATH).read_bytes()),text)
 def test_build_changed_source_rejected(self):
  with self.assertRaises(w.IsolationError):c.build(b'print(1)','a'*64,REGISTERED)
 def test_exact_job_and_no_secrets(self):
  v,p=self.job();self.assertEqual(c.validate_job(v,p,REGISTERED)['configured_sql_secrets'],0);self.assertEqual(v['properties']['configuration']['replicaRetryLimit'],0)
 def test_extra_secret_rejected(self):
  v,p=self.job();v['properties']['configuration']['secrets']=[{'name':'sql','value':'synthetic'}]
  with self.assertRaises(w.IsolationError):c.validate_job(v,p,REGISTERED)
 def test_sql_environment_rejected(self):
  v,p=self.job();v['properties']['template']['containers'][0]['env'].append(dict(name='DATABASE_URL',value='synthetic'))
  with self.assertRaises(w.IsolationError):c.validate_job(v,p,REGISTERED)
 def test_sidecar_rejected(self):
  v,p=self.job();v['properties']['template']['containers'].append(copy.deepcopy(v['properties']['template']['containers'][0]))
  with self.assertRaises(w.IsolationError):c.validate_job(v,p,REGISTERED)
 def test_empty_native_execution_alias_only(self):
  wanted=self.plan()['definition']['properties']['template'];v=dict(copy.deepcopy(wanted),initContainers=[],volumes=[]);c.template_checked(v,wanted,execution=True)
  with self.assertRaises(w.IsolationError):c.template_checked(v,wanted)
 def test_consumed_stage_no_azure_call(self):
  with tempfile.TemporaryDirectory() as d,patch.object(c,'FOLDER',Path(d)):
   (Path(d)/'start-intent.json').write_text('{}');client=Mock()
   with self.assertRaises(w.IsolationError):c.dispatch(client,'start')
   client.call.assert_not_called()
 def test_no_cleanup_or_arbitrary_write(self):
  with self.assertRaises(w.IsolationError):c.effect('cleanup',None,None)
 def test_extra_role_rejected(self):
  client=Mock();client.call.return_value=dict(value=[dict(id='wrong',properties={})])
  with self.assertRaises(w.IsolationError):c.direct_roles(client,PRINCIPAL,[])
 def test_unknown_execution_denied(self):
  with self.assertRaises(w.IsolationError):c.execution_name('old-job-abcdefg')
 def test_frozen_native_jobs_use_separate_readonly_transport(self):
  client=Mock();client.call.return_value=dict(id='synthetic')
  values=c.frozen_native_jobs(client);self.assertEqual(set(values),set(c.FROZEN_JOBS));self.assertEqual(client.call.call_count,8)
  self.assertTrue(all(call.args[0]=='GET' and call.args[2]=='2025-07-01' for call in client.call.call_args_list))
 def test_frozen_job_write_not_authorized(self):
  client=c.Transport()
  with self.assertRaises(w.IsolationError):client.call('PUT',w.BASE+'Microsoft.App/jobs/'+c.FROZEN_JOBS[0],'2025-07-01',body={},stage='identity')
 def test_full_family_query_not_prefix_filtered(self):
  run=dict(name=w.JOB+'-abcdefg',properties=dict(startTime='2026-10-05T13:00:00Z'));q=c.family_query(run)
  self.assertIn('2026-10-05T13:15:00',q);self.assertNotIn('Log_s startswith',q);self.assertNotIn('take',q);self.assertNotIn('distinct',q)
 def test_partial_extra_log_tables_rejected(self):
  with self.assertRaises(w.IsolationError):c.logs_response(dict(tables=[],error={}),w.JOB+'-abcdefg')
 def test_no_partial_worker_pass(self):
  with self.assertRaises(w.IsolationError):c.validate_result(dict(status='model_isolation_trial_passed'))
 def test_import_has_no_linux_or_network_effects(self):
  import subprocess
  raw=(c.ROOT/w.PATH).read_text();bootstrap='import sys,json\nscope={"__name__":"isolated_import"}\nexec(compile('+repr(raw)+',"worker","exec"),scope)\nprint(json.dumps({"status":"imported","runtime_called":False}))'
  result=subprocess.run([sys.executable,'-I','-B','-c','import sys;exec(sys.stdin.read())'],input=bootstrap,capture_output=True,text=True,timeout=15)
  self.assertEqual(result.returncode,0);self.assertEqual(json.loads(result.stdout),dict(status='imported',runtime_called=False))

if __name__=='__main__':unittest.main()
