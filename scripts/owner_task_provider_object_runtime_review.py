"""Fixed provider-object metadata review. No monitoring/source values or grants."""
import base64,hashlib,json,lzma,re,secrets
from psycopg import sql
import owner_task_isolated_tls_view_observation as tls
from ibkr_etl import owner_codex_isolated_target as target

OPERATION='cb085a51-d6c1-4d1d-819f-031f09a2d4d3'
JOB='ibkrv2stg-ownobjv-20261005'
JOB_ID=target.BASE+'Microsoft.App/jobs/'+JOB
PATH='scripts/owner_task_provider_object_runtime_review.py'
PREFIX='OWNER_TASK_PROVIDER_OBJECT_RUNTIME_REVIEW_V1 '
WIRE_PREFIX='OWNER_TASK_PROVIDER_OBJECT_REVIEWED_WIRE_V1 '
canonical=tls.canonical
require=tls.require
COLLECTOR=tls.prior.boundary.collector
FIXTURE_SHA='9aa54f84f2b59dd1c18fb9535bf81d7249df13f6dc4a589436df3940460897e6'
MAX_RAW=4*1024*1024
MAX_PACKED=512*1024
RELATION_SQL="""SELECT c.oid::int8 AS oid,n.nspname AS schema,c.relname AS name,c.relkind::text AS kind,
pg_catalog.has_schema_privilege(current_user,n.oid,'USAGE') AS schema_usage,
pg_catalog.has_table_privilege(current_user,c.oid,'SELECT') AS select_access,
pg_catalog.has_any_column_privilege(current_user,c.oid,'SELECT') AS column_select_access,
pg_catalog.has_table_privilege(current_user,c.oid,'INSERT') AS insert_access,
pg_catalog.has_table_privilege(current_user,c.oid,'UPDATE') AS update_access,
pg_catalog.has_table_privilege(current_user,c.oid,'DELETE') AS delete_access,
pg_catalog.has_table_privilege(current_user,c.oid,'TRUNCATE') AS truncate_access,
pg_catalog.has_table_privilege(current_user,c.oid,'TRIGGER') AS trigger_access
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')
AND n.nspname !~ '^pg_(toast_)?temp_[0-9]+$' AND c.relkind IN ('r','p','v','m','f')
ORDER BY c.oid LIMIT 4097"""
ROUTINE_SQL="""SELECT p.oid::int8 AS oid,n.nspname AS schema,p.proname AS name,
pg_catalog.pg_get_function_identity_arguments(p.oid) AS arguments,p.prosecdef AS security_definer,
pg_catalog.has_schema_privilege(current_user,n.oid,'USAGE') AS schema_usage,
pg_catalog.has_function_privilege(current_user,p.oid,'EXECUTE') AS execute_access
FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
ORDER BY p.oid LIMIT 4097"""
SCHEMA_SQL="""SELECT n.oid::int8 AS oid,n.nspname AS name,
pg_catalog.has_schema_privilege(current_user,n.oid,'USAGE') AS usage,
pg_catalog.has_schema_privilege(current_user,n.oid,'CREATE') AS create_access
FROM pg_catalog.pg_namespace n ORDER BY n.oid LIMIT 129"""
SEQUENCE_SQL="""SELECT c.oid::int8 AS oid,n.nspname AS schema,c.relname AS name,
pg_catalog.has_schema_privilege(current_user,n.oid,'USAGE') AS schema_usage,
pg_catalog.has_sequence_privilege(current_user,c.oid,'USAGE') AS usage,
pg_catalog.has_sequence_privilege(current_user,c.oid,'SELECT') AS select_access,
pg_catalog.has_sequence_privilege(current_user,c.oid,'UPDATE') AS update_access
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')
AND n.nspname !~ '^pg_(toast_)?temp_[0-9]+$' AND c.relkind='S'
ORDER BY c.oid LIMIT 4097"""

def validate_access(rows,fields,booleans,limit):
 require(type(rows)is list and len(rows)<=limit and all(type(r)is dict and set(r)==fields
  and type(r['oid'])is int and 0<r['oid']<=4294967295
  and all(type(r[k])is bool for k in booleans)
  and all(type(r[k])is str and len(r[k].encode())<=4096 and all(z.isprintable() for z in r[k]) for k in fields-booleans-{'oid'}) for r in rows),'provider_object_access_invalid')
 require([r['oid'] for r in rows]==sorted({r['oid'] for r in rows}),'provider_object_access_invalid')
 return rows

def identity(raw,database):
 require(database in tls.prior.boundary.PROVIDER_DATABASES,'provider_object_target_invalid')
 got=raw.execute('SELECT session_user::text AS login,current_user::text AS role,current_database() AS database,current_setting(\'transaction_read_only\') AS readonly,current_setting(\'search_path\') AS path,pg_my_temp_schema() AS temporary_schema').fetchone()
 require(got==dict(login=target.LOGIN,role=target.LOGIN,database=database,readonly='on',path='pg_catalog',temporary_schema=0),'provider_object_identity_invalid')
 return got

def observe(raw,database):
 with raw.transaction():
  raw.execute('SET TRANSACTION READ ONLY')
  who=identity(raw,database)
  catalog={k:raw.execute(spec[3]).fetchall() for k,spec in COLLECTOR.SPECS.items()}
  COLLECTOR._validate_catalog(catalog)
  relfields={'oid','schema','name','kind','schema_usage','select_access','column_select_access','insert_access','update_access','delete_access','truncate_access','trigger_access'}
  relations=validate_access(raw.execute(RELATION_SQL).fetchall(),relfields,relfields-{'oid','schema','name','kind'},4096)
  routinefields={'oid','schema','name','arguments','security_definer','schema_usage','execute_access'}
  routines=validate_access(raw.execute(ROUTINE_SQL).fetchall(),routinefields,{'security_definer','schema_usage','execute_access'},4096)
  schemas=validate_access(raw.execute(SCHEMA_SQL).fetchall(),{'oid','name','usage','create_access'},{'usage','create_access'},128)
  seqfields={'oid','schema','name','schema_usage','usage','select_access','update_access'}
  sequences=validate_access(raw.execute(SEQUENCE_SQL).fetchall(),seqfields,seqfields-{'oid','schema','name'},4096)
  require({r['oid'] for r in routines}=={r['oid'] for r in catalog['routines']}
   and {r['oid'] for r in schemas}=={r['oid'] for r in catalog['schemas']}
   and {r['oid'] for r in relations}=={r['oid'] for r in catalog['relations'] if r['kind'] in ('r','p','v','m','f')}
   and {r['oid'] for r in sequences}=={r['oid'] for r in catalog['relations'] if r['kind']=='S'},'provider_object_coverage_invalid')
  # This phase only observes metadata. Even LIMIT 0 on an unreviewed view or
  # invoking a routine could have side effects. No provider object is invoked.
  return dict(database=database,identity=who,catalog=catalog,effective_relations=relations,
   effective_routines=routines,effective_schemas=schemas,effective_sequences=sequences,
   provider_object_values_read=0,routines_invoked=0,ddl_or_dml_executed=0,
   source_rows_read=0,source_rows_written=0,sensitive_access_acceptance=False)

def frames(record):
 require(type(record)is dict and record.get('database') in tls.prior.boundary.PROVIDER_DATABASES,'provider_object_wire_target_invalid')
 data=canonical(record);require(len(data)<=MAX_RAW,'provider_object_wire_oversized')
 packed=lzma.compress(data,preset=6);require(len(packed)<=MAX_PACKED,'provider_object_wire_oversized')
 encoded=base64.b64encode(packed).decode('ascii');parts=[encoded[i:i+900] for i in range(0,len(encoded),900)]
 require(0<len(parts)<=800,'provider_object_wire_oversized')
 binding=dict(operation_id=OPERATION,database=record['database'],record_sha256=hashlib.sha256(data).hexdigest(),
  compressed_sha256=hashlib.sha256(packed).hexdigest(),raw_bytes=len(data),compressed_bytes=len(packed),frame_count=len(parts))
 return [dict(binding,index=i,data=v) for i,v in enumerate(parts)]

def run(admin_secret,*,connect,emit):
 result=dict(status='unaccepted',operation_id=OPERATION,job_resource_id=JOB_ID,host=target.HOST,
  database=target.DATABASE,credential_rotations=0,fixture_rows_written=0,restricted_crud_repeated=False,
  roles_created=0,grants_changed=0,production_changed=False,source_rows_read=0,source_rows_written=0,
  provider_object_values_read=0,routines_invoked=0,model_calls=0,agents_activated=0,
  provider_boundary_accepted=False,model_credential_isolation_verified=False,step_2_complete=False,automatic_retry=False)
 admin=None
 try:
  params=tls.prior.parser.validate_admin_secret(admin_secret);tls.prior.parser.require_ca_bundle()
  admin=tls.connect_exact(connect,params,target.DATABASE);tls.prior._transport(admin,dict(params,dbname=target.DATABASE))
  before=tls.fixture_fingerprint(admin);result['fixture_before']=before
  require(before['registered_scope_state_sha256']==FIXTURE_SHA,'provider_object_fixture_changed')
  password=secrets.token_urlsafe(48)
  verifier=admin.pgconn.encrypt_password(password.encode(),target.LOGIN.encode(),b'scram-sha-256')
  tls.preparation.validate_scram_verifier(verifier)
  with admin.transaction():
   admin.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(sql.Identifier(target.LOGIN),sql.Literal(verifier.decode('ascii'))))
   tls.preparation.validate_registered_metadata(tls.preparation.scope_metadata(admin),login_enabled=True)
  result['credential_rotations']=1
  restricted=dict(params,user=target.LOGIN,password=password,dbname=target.DATABASE)
  proof=tls.connect_exact(connect,restricted,target.DATABASE)
  try:result['positive_tls']=tls.own_login_tls(proof,restricted)
  finally:proof.close()
  summaries=[]
  for database in sorted(tls.prior.boundary.PROVIDER_DATABASES):
   raw=tls.connect_exact(connect,restricted,database)
   try:
    tls.prior._transport(raw,dict(restricted,dbname=database));record=observe(raw,database)
   finally:raw.close()
   wire=frames(record)
   for frame in wire:emit(WIRE_PREFIX+canonical(frame).decode('ascii'),flush=True)
   relations=record['effective_relations'];routines=record['effective_routines']
   summaries.append(dict(database=database,record_sha256=wire[0]['record_sha256'],frame_count=len(wire),
    relation_count=len(relations),routine_count=len(routines),
    accessible_relation_count=sum(r['schema_usage'] and (r['select_access'] or r['column_select_access']) for r in relations),
    writable_relation_count=sum(r['schema_usage'] and any(r[k] for k in ('insert_access','update_access','delete_access','truncate_access','trigger_access')) for r in relations),
    executable_non_catalog_routine_count=sum(r['schema'] not in ('pg_catalog','information_schema') and r['schema_usage'] and r['execute_access'] for r in routines),
    executable_security_definer_count=sum(r['security_definer'] and r['schema_usage'] and r['execute_access'] for r in routines),
    object_and_routine_semantic_review_complete=False))
  result['provider_summaries']=summaries
  after=tls.fixture_fingerprint(admin);result['fixture_after']=after
  require(before==after,'provider_object_fixture_changed');result['fixture_unchanged']=True
  result['status']='provider_object_metadata_completed_semantic_review_pending'
  require(password.encode() not in canonical(result) and admin_secret.encode() not in canonical(result),'provider_object_sensitive_output')
 except Exception as error:
  code=str(error) if isinstance(error,ValueError) else ''
  result['reason']=code if re.fullmatch('(provider_object|isolated_sql)_[a-z_]+',code) else 'provider_object_observation_unaccepted'
 finally:
  if admin is not None:admin.close()
 require(len(canonical(result))<8192,'provider_object_result_oversized')
 return result

def main(admin_secret,migration_text,*,connect,emit=print):
 require(hashlib.sha256(migration_text.encode()).hexdigest()==tls.preparation.MIGRATION_SHA256,'provider_object_migration_changed')
 result=run(admin_secret,connect=connect,emit=emit);emit(PREFIX+canonical(result).decode('ascii'),flush=True);return result

def build(source,hashes,runtime):
 import owner_task_isolated_sql_root_launch as root
 paths=set(root.SOURCE_PATHS)|{tls.PATH,PATH}
 require(type(source)is dict and type(hashes)is dict and set(source)==set(hashes)==paths
  and all(type(v)is bytes and hashlib.sha256(v).hexdigest()==hashes[k] for k,v in source.items()),'provider_object_sources_invalid')
 base=root.build({k:source[k] for k in root.SOURCE_PATHS},{k:hashes[k] for k in root.SOURCE_PATHS},runtime)
 decoded={k:v.decode('utf-8') for k,v in source.items()}
 for path,text in decoded.items():
  if path.endswith('.py'):compile(text,path,'exec')
 modules=root.SOURCE_MODULES+((tls.PATH,'owner_task_isolated_tls_view_observation'),(PATH,'owner_task_provider_object_runtime_review'))
 launcher=root.LAUNCHER.replace('sys.modules["run_owner_task_isolated_sql_root"].main','sys.modules["owner_task_provider_object_runtime_review"].main',1).replace('OWNER_TASK_ISOLATED_SQL_ROOT_LAUNCH_FAILURE_V1 ','OWNER_TASK_PROVIDER_OBJECT_REVIEWED_LAUNCH_FAILURE_V1 ')
 script=('_sources='+repr(decoded)+'\n_hashes='+repr(hashes)+'\n_modules='+repr(modules)+'\n_runtime='+repr(runtime)+'\n_migration='+repr(root.MIGRATION)+'\n'+launcher)
 packed=base64.b85encode(lzma.compress(script.encode(),preset=9)).decode('ascii').replace(chr(36),':')
 definition=json.loads(canonical(base['definition']))
 definition['tags'].update(purpose='owner-provider-object-observation',operation=OPERATION,sourceSha256=root.digest(hashes),fullSourceSha256=hashlib.sha256(script.encode()).hexdigest())
 args=['-I','-B','-c',"import base64,lzma;exec(compile(lzma.decompress(base64.b85decode('"+packed+"'.replace(':',chr(36)))),'provider-object-observation','exec'))"]
 require(len(canonical(args))<=65536 and len(JOB)<=32 and chr(36) not in canonical(args).decode(),'provider_object_args_invalid')
 definition['properties']['template']['containers'][0]['args']=args
 return dict(definition=definition,definition_sha256=root.digest(definition),source_hashes=hashes,expected_runtime=runtime,step_2_complete=False)
