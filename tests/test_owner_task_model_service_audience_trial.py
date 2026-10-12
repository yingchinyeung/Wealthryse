import base64,copy,io,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import owner_task_model_service_audience_trial as w
import deploy_owner_task_model_service_audience_trial as c
import test_owner_task_model_isolation_trial as fixture

class ServiceAudienceTests(unittest.TestCase):
 def token(self,changes=None):
  claims=dict(aud='00000017-0000-0000-c000-000000000000',tid=w.TENANT,oid=fixture.PRINCIPAL,appid=fixture.CLIENT,exp=int(time.time())+600);claims.update(changes or {})
  bearer='synthetic.'+base64.urlsafe_b64encode(w.canonical(claims)).decode().rstrip('=')+'.signature'
  reply=Mock();reply.status=200;reply.__enter__=Mock(return_value=reply);reply.__exit__=Mock(return_value=False);reply.read.return_value=w.canonical(dict(access_token=bearer))
  env=fixture.ModelIsolationTests().environment()
  with patch.object(w,'build_opener') as opener:
   opener.return_value.open.return_value=reply;return w.token(env,fixture.CLIENT,fixture.PRINCIPAL,'https://vault.azure.net')
 def test_resource_id_audience_not_uri_only(self):self.assertTrue(self.token().startswith('synthetic.'))
 def test_identity_bindings_remain_mandatory(self):
  for changes in (dict(oid=fixture.CLIENT),dict(tid=fixture.CLIENT),dict(appid=fixture.PRINCIPAL),dict(exp=0),dict(exp='9999999999'),dict(aud=None),dict(aud='')):
   with self.subTest(changes=changes),self.assertRaises(w.IsolationError):self.token(changes)
 def test_destination_rejects_invalid_token_not_false_denial(self):
  err=HTTPError(w.SECRET_URL,401,'synthetic',{},io.BytesIO(w.canonical(dict(error=dict(code='Unauthorized')))))
  with patch.object(w,'build_opener') as opener,self.assertRaises(w.IsolationError):
   opener.return_value.open.side_effect=err;w.denial(w.SECRET_URL,'synthetic','secret')
 def test_wrong_requested_resource_still_rejected(self):
  with self.assertRaises(w.IsolationError):w.identity_request(fixture.ModelIsolationTests().environment(),fixture.CLIENT,'https://graph.microsoft.com')
 def test_previous_800_sources_preserved(self):
  before={str(i):'a'*64 for i in range(800)};c.sources_valid(before,dict(before,**{k:'b'*64 for k in c.NAMES}))
 def test_old_source_or_extra_mutation_rejected(self):
  before={str(i):'a'*64 for i in range(800)};after=dict(before,**{k:'b'*64 for k in c.NAMES});after['0']='c'*64
  with self.assertRaises(w.IsolationError):c.sources_valid(before,after)
 def test_new_job_uses_same_identity_not_old_job(self):
  sources=c.old.prior.host.sources();plan=c.build(sources,fixture.REGISTERED)
  self.assertEqual(plan['definition']['tags']['operation'],w.OPERATION);self.assertEqual(w.MI,c.old.MI);self.assertNotEqual(c.JOB,c.old.JOB)
 def test_frozen_old_worker_still_rejects_guid_audience(self):
  claims=dict(aud='00000017-0000-0000-c000-000000000000',tid=w.TENANT,oid=fixture.PRINCIPAL,appid=fixture.CLIENT,exp=int(time.time())+600)
  bearer='synthetic.'+base64.urlsafe_b64encode(w.canonical(claims)).decode().rstrip('=')+'.signature'
  reply=Mock();reply.status=200;reply.__enter__=Mock(return_value=reply);reply.__exit__=Mock(return_value=False);reply.read.return_value=w.canonical(dict(access_token=bearer))
  with patch.object(c.old.worker,'build_opener') as opener,self.assertRaises(c.old.worker.IsolationError):
   opener.return_value.open.return_value=reply;c.old.worker.token(fixture.ModelIsolationTests().environment(),fixture.CLIENT,fixture.PRINCIPAL,'https://vault.azure.net')
  self.assertIn("claims.get('aud')==audience",(c.old.ROOT/c.old.worker.PATH).read_text())
 def test_new_source_import_is_inert(self):
  import subprocess
  source=(c.old.ROOT/w.PATH).read_text();code='scope={"__name__":"source_import"}\nexec(compile('+repr(source)+',"trial","exec"),scope)\nprint("imported")'
  result=subprocess.run([sys.executable,'-I','-B','-c','import sys;exec(sys.stdin.read())'],input=code,capture_output=True,text=True,timeout=15)
  self.assertEqual(result.returncode,0);self.assertEqual(result.stdout.strip(),'imported')
 def test_consumed_new_start_not_repeated(self):
  with tempfile.TemporaryDirectory() as d,patch.object(c.old,'FOLDER',Path(d)):
   (Path(d)/'serviceaud-start-intent.json').write_text('{}');client=Mock()
   with self.assertRaises(w.IsolationError):c.dispatch(client,'start')
   client.call.assert_not_called()
 def test_arbitrary_azure_route_rejected(self):
  with self.assertRaises(w.IsolationError):c.Transport().call('GET',c.old.JOB)

if __name__=='__main__':unittest.main()
