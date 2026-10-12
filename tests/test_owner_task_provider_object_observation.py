import base64,hashlib,json,lzma,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import owner_task_provider_object_observation as w
import deploy_owner_task_provider_object_observation as c

class ProviderObjectsTests(unittest.TestCase):
 def test_three_additions_preserve_789_frozen_sources(self):
  before={str(i):'a'*64 for i in range(789)}
  c.sources_valid(before,dict(before,**{k:'b'*64 for k in c.ADDITIONS}))
 def test_previous_hash_drift_rejected(self):
  before={str(i):'a'*64 for i in range(789)}
  after=dict(before,**{k:'b'*64 for k in c.ADDITIONS});after['0']='c'*64
  with self.assertRaises(Exception):c.sources_valid(before,after)
 def test_unregistered_addition_rejected(self):
  before={str(i):'a'*64 for i in range(789)}
  with self.assertRaises(Exception):c.sources_valid(before,dict(before,**{k:'b'*64 for k in c.ADDITIONS},unknown='a'*64))
 def test_fixed_full_bundle_lossless_and_dollar_free(self):
  import test_owner_task_isolated_sql as fixture
  runtime=fixture.runtime()
  paths=(*c.bundle.SOURCE_PATHS,w.tls.PATH,w.PATH)
  source={k:(c.ROOT/k).read_bytes() for k in paths}
  plan=w.build(source,{k:hashlib.sha256(v).hexdigest() for k,v in source.items()},runtime)
  arg=plan['definition']['properties']['template']['containers'][0]['args'][-1]
  self.assertNotIn('$',arg);self.assertLessEqual(len(w.canonical(plan['definition']['properties']['template']['containers'][0]['args'])),65536)
  packed=arg.split("base85decode('")[-1] if "base85decode('" in arg else arg.split("b85decode('")[1]
  packed=packed.split("'.replace")[0]
  text=lzma.decompress(base64.b85decode(packed.replace(':','$'))).decode()
  self.assertIn('owner_task_provider_object_observation',text);self.assertIn(w.OPERATION,text)
  compile(text,'bundled-observer','exec')
  self.assertEqual(plan['source_hashes'],{k:hashlib.sha256(v).hexdigest() for k,v in source.items()})
 def test_build_source_hash_mutation_rejected(self):
  with self.assertRaises(Exception):w.build({}, {}, {})
 def test_transport_previous_job_route_rejected(self):
  t=c.Transport()
  with self.assertRaises(Exception):t.call('GET',c.view_prior_control.JOB)
 def test_unknown_effect_stage_rejected(self):
  with self.assertRaises(Exception):c.effect('cleanup',{})
 def test_consumed_start_cannot_restart(self):
  with tempfile.TemporaryDirectory() as d,patch.object(c,'FOLDER',Path(d)):
   (Path(d)/'start-intent.json').write_text('{}')
   with self.assertRaises(Exception):c.dispatch(object(),'start')
 def test_wire_complete_lossless_bindings(self):
  record={'database':'azure_sys','metadata':[{'oid':i,'name':'synthetic-'+str(i)} for i in range(200)]}
  frames=w.frames(record)
  packed=base64.b64decode(''.join(f['data'] for f in frames),validate=True)
  raw=lzma.decompress(packed)
  self.assertEqual(json.loads(raw),record)
  self.assertTrue(all(f['operation_id']==w.OPERATION and f['frame_count']==len(frames) and f['record_sha256']==hashlib.sha256(raw).hexdigest() and f['compressed_sha256']==hashlib.sha256(packed).hexdigest() for f in frames))
  self.assertEqual([f['index'] for f in frames],list(range(len(frames))))
 def test_wire_unknown_database_rejected(self):
  with self.assertRaises(Exception):w.frames({'database':w.target.DATABASE})
 def test_wire_raw_overflow_rejected(self):
  with self.assertRaises(Exception):w.frames({'database':'azure_sys','x':'x'*w.MAX_RAW})
 def test_access_exact_types(self):
  fields={'oid','schema','name','select_access'}
  row={'oid':1,'schema':'public','name':'synthetic','select_access':False}
  self.assertEqual(w.validate_access([row],fields,{'select_access'},1),[row])
  for change in ({'oid':True},{'select_access':0},{'extra':False},{'name':'bad\nname'}):
   with self.assertRaises(Exception):w.validate_access([dict(row,**change)],fields,{'select_access'},1)
 def test_access_no_sampling_or_duplicates(self):
  fields={'oid','name','usage'};row={'oid':1,'name':'public','usage':False}
  for rows in ([row,row],[dict(row,oid=2),row]):
   with self.assertRaises(Exception):w.validate_access(rows,fields,{'usage'},2)
  with self.assertRaises(Exception):w.validate_access([row],fields,{'usage'},0)
 def test_identity_requires_real_restricted_login_and_readonly(self):
  expected=dict(login=w.target.LOGIN,role=w.target.LOGIN,database='azure_sys',readonly='on',path='pg_catalog',temporary_schema=0)
  class Raw:
   value=expected
   def execute(self,*args):return self
   def fetchone(self):return self.value
  raw=Raw();self.assertEqual(w.identity(raw,'azure_sys'),expected)
  for key,value in [('login',w.target.ADMIN),('role',w.target.CAPABILITY),('readonly','off'),('path','public'),('temporary_schema',1)]:
   raw.value=dict(expected,**{key:value})
   with self.assertRaises(Exception):w.identity(raw,'azure_sys')
 def test_identity_unknown_database_precedes_query(self):
  with self.assertRaises(Exception):w.identity(object(),w.target.DATABASE)
 def test_metadata_queries_are_catalog_only(self):
  queries=[x[3] for x in w.COLLECTOR.SPECS.values()]+[w.RELATION_SQL,w.ROUTINE_SQL,w.SCHEMA_SQL,w.SEQUENCE_SQL]
  for query in queries:
   self.assertTrue(query.startswith('SELECT '));self.assertIn('pg_catalog.',query)
   self.assertNotIn('query_store.',query);self.assertNotIn('azure_monitor.',query)
   self.assertNotIn('pg_stat_activity',query);self.assertNotIn('pg_read_file',query)
   self.assertIn(' LIMIT ',query)
 def test_fixture_drift_precedes_rotation_or_wire(self):
  class Raw:
   def close(self):pass
  with patch.object(w.tls.prior.parser,'validate_admin_secret',return_value={}),patch.object(w.tls.prior.parser,'require_ca_bundle'),patch.object(w.tls,'connect_exact',return_value=Raw()),patch.object(w.tls.prior,'_transport'),patch.object(w.tls,'fixture_fingerprint',return_value={'registered_scope_state_sha256':'a'*64}):
   out=w.run('test-secret',connect=object(),emit=lambda *a,**k:self.fail('wire after drift'))
  self.assertEqual(out['reason'],'provider_object_fixture_changed');self.assertEqual(out['credential_rotations'],0)
  self.assertFalse(out['provider_boundary_accepted']);self.assertFalse(out['step_2_complete'])

if __name__=='__main__':unittest.main()
