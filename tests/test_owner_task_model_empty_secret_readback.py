import copy,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import owner_task_model_empty_secret_readback as r
import test_owner_task_model_isolation_trial as fixture
REGISTERED=fixture.REGISTERED

class EmptySecretTests(unittest.TestCase):
 def job(self):return fixture.ModelIsolationTests().job()
 def test_null_only_detached_readback(self):
  native,plan=self.job();native['properties']['configuration']['secrets']=None;before=copy.deepcopy(native)
  result=r.validate_job(native,plan,REGISTERED);self.assertEqual(native,before);self.assertTrue(result['only_null_for_empty_secrets_descriptor_mapped']);self.assertEqual(result['native_job_sha256'],r.c.digest(native))
 def test_original_empty_still_valid(self):
  native,plan=self.job();self.assertFalse(r.validate_job(native,plan,REGISTERED)['only_null_for_empty_secrets_descriptor_mapped'])
 def test_any_actual_binding_rejected(self):
  for secret in ([dict(name='sql')],[dict(name='sql',value='synthetic')],{},'',False):
   native,plan=self.job();native['properties']['configuration']['secrets']=secret
   with self.assertRaises(Exception):r.validate_job(native,plan,REGISTERED)
 def test_missing_secret_descriptor_rejected(self):
  native,plan=self.job();native['properties']['configuration'].pop('secrets')
  with self.assertRaises(Exception):r.validate_job(native,plan,REGISTERED)
 def test_unrelated_environment_drift_rejected(self):
  native,plan=self.job();native['properties']['configuration']['secrets']=None;native['properties']['template']['containers'][0]['env'].append(dict(name='DATABASE_URL',value='synthetic'))
  with self.assertRaises(Exception):r.validate_job(native,plan,REGISTERED)
 def test_798_hashes_preserved(self):
  before={str(i):'a'*64 for i in range(798)};r.source_valid(before,dict(before,**{k:'b'*64 for k in r.ADDITIONS}))
 def test_old_source_or_unknown_addition_rejected(self):
  before={str(i):'a'*64 for i in range(798)};after=dict(before,**{k:'b'*64 for k in r.ADDITIONS});after['0']='c'*64
  with self.assertRaises(Exception):r.source_valid(before,after)
  after['0']='a'*64;after['extra']='c'*64
  with self.assertRaises(Exception):r.source_valid(before,after)
 def test_start_consumed_precedes_read_or_dispatch(self):
  with tempfile.TemporaryDirectory() as d,patch.object(r.c,'FOLDER',Path(d)):
   (Path(d)/'start-intent.json').write_text('{}');client=Mock()
   with self.assertRaises(Exception):r.start(client)
   client.call.assert_not_called()
 def test_readback_does_not_retarget_frozen_globals(self):
  self.assertEqual(r.c.worker.JOB,'ibkrv2stg-ownmodel-20261005');self.assertEqual(r.c.worker.OPERATION,'eb757d68-f32e-4f94-b285-c79d9cad5b57')

if __name__=='__main__':unittest.main()
