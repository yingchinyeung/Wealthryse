"""New qualification of the prepared synthetic fixture, never a setup retry.

The prior password existed only in the ended trusted process. After fresh
marker/ACL/fixture checks, rotate that one disposable LOGIN credential, then
run the unchanged restricted harness with correctly escaped startup options.
No production/source data, grant, role creation, cleanup, model or retry.
"""
import base64
import hashlib
import json
import lzma
import re
import secrets

from psycopg import sql
from psycopg.rows import dict_row
from ibkr_etl import owner_codex_isolated_target as target
import run_owner_task_isolated_sql_root as prior
import owner_task_isolated_scope_diagnostic as diagnosis
import owner_task_isolated_permissions as permissions
import prepare_owner_task_isolated_trial as preparation

OPERATION='06078342-70df-45d9-b69e-6cec37710780'
JOB='ibkrv2stg-ownsafe-20261005'
JOB_ID=target.BASE+'Microsoft.App/jobs/'+JOB
PREFIX='OWNER_TASK_ISOLATED_SQL_SAFE_ENCODING_V1 '
PATH='scripts/owner_task_isolated_sql_safe_encoding.py'


def canonical(value):return prior.canonical(value)
def require(value,code):
    if not value:raise ValueError(code)


def connect_exact(connect,parameters,database,*,restricted=False):
    require(type(parameters)is dict and parameters.get('host')==target.HOST
        and parameters.get('user') in (target.ADMIN,target.LOGIN)
        and parameters.get('sslmode')=='verify-full' and parameters.get('sslrootcert')==prior.CA_PATH
        and database in {'postgres',target.DATABASE}|prior.boundary.PROVIDER_DATABASES,
        'resume_connection_scope_invalid')
    options='-c search_path=pg_catalog -c statement_timeout=45000 -c lock_timeout=3000 -c idle_in_transaction_session_timeout=45000'
    if restricted and database==target.DATABASE:
        # libpq/server options split unescaped whitespace. Preserve the exact
        # harness representation without issuing a post-connect role change.
        options+=' -c role='+target.CAPABILITY+' -c search_path='+target.SCHEMA+',\\ pg_catalog'
    return connect(**dict(parameters,dbname=database),connect_timeout=20,autocommit=True,
        row_factory=dict_row,options=options,application_name='ownertrial-isolated-sql-resume')


def verify_fixture(raw):
    preparation.bind_trusted_connection(raw,target.DATABASE)
    preparation.validate_registered_metadata(preparation.scope_metadata(raw),login_enabled=True)
    manifest=permissions.verify_permissions(raw,permissions.PermissionTarget(),
        installer_identity=target.ADMIN,require_fresh_login=False,login_enabled=True)
    p=preparation
    owners=[(p.TENANT_A,p.OWNER_A),(p.TENANT_A,p.OWNER_B),(p.TENANT_B,p.OWNER_A)]
    with raw.transaction():
        raw.execute('SET TRANSACTION READ ONLY')
        raw.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(target.SCHEMA_OWNER)))
        q=sql.SQL('SELECT tenant_id::text AS tenant_id FROM {}.tenants ORDER BY tenant_id').format(sql.Identifier(target.SCHEMA))
        require(raw.execute(q).fetchall()==[{'tenant_id':p.TENANT_A},{'tenant_id':p.TENANT_B}],
            'resume_fixture_tenants_changed')
        q=sql.SQL('SELECT tenant_id::text AS tenant_id,owner_object_id::text AS owner_object_id,active,role_name,registered_subject_ids,authorized_subject_ids FROM {}.owner_task_authority_fixture ORDER BY tenant_id,owner_object_id').format(sql.Identifier(target.SCHEMA))
        expected=[dict(tenant_id=t,owner_object_id=o,active=True,role_name='owner',
            registered_subject_ids=['SYNTH_A','SYNTH_B'],authorized_subject_ids=['SYNTH_A','SYNTH_B']) for t,o in owners]
        require(raw.execute(q).fetchall()==expected,'resume_fixture_authority_changed')
        q=sql.SQL('SELECT tenant_id::text AS tenant_id,worker_id::text AS worker_id,service_principal_id::text AS service_principal_id,enabled FROM {}.owner_task_worker_fixture ORDER BY tenant_id').format(sql.Identifier(target.SCHEMA))
        require(raw.execute(q).fetchall()==[dict(tenant_id=t,worker_id=p.WORKER,
            service_principal_id=p.PRINCIPAL,enabled=True) for t in (p.TENANT_A,p.TENANT_B)],
            'resume_fixture_worker_changed')
        for tenant,owner in owners:
            raw.execute("SELECT pg_catalog.set_config('app.tenant_id',%s,true)",(tenant,))
            raw.execute("SELECT pg_catalog.set_config('app.owner_object_id',%s,true)",(owner,))
            for table in permissions.TASK_TABLES:
                row=raw.execute(sql.SQL('SELECT count(*)::integer AS count FROM {}.{}').format(
                    sql.Identifier(target.SCHEMA),sql.Identifier(table))).fetchone()
                require(row=={'count':0},'resume_registered_owner_scope_not_empty')
    return dict(status='passed',registered_owner_scopes_verified=3,existing_privileges_verified=manifest,
        all_unknown_owner_scopes_claimed_empty=False,fixture_data_modified=False)


def run(admin_secret,*,connect):
    result=dict(status='unaccepted',operation_id=OPERATION,job_resource_id=JOB_ID,
        host=target.HOST,database=target.DATABASE,source_rows_read=0,source_rows_written=0,
        production_changed=False,roles_created=0,grants_changed=0,database_recreated=False,
        credential_rotations=0,fresh_restricted_login_verified=False,
        model_credential_isolation_verified=False,step_2_complete=False,
        model_calls=0,agents_activated=0,automatic_retry=False)
    admin=None
    try:
        parameters=prior.parser.validate_admin_secret(admin_secret); prior.parser.require_ca_bundle()
        observed=diagnosis.run(admin_secret,connect=connect)
        require(observed['status']=='readonly_scope_diagnostic_completed','resume_diagnostic_unaccepted')
        admin=connect_exact(connect,parameters,target.DATABASE)
        prior._transport(admin,dict(parameters,dbname=target.DATABASE))
        result['fixture_review']=verify_fixture(admin)
        password=secrets.token_urlsafe(48)
        require(re.fullmatch('[A-Za-z0-9_-]{48,128}',password),'resume_password_shape_invalid')
        encrypted=admin.pgconn.encrypt_password(password.encode(),target.LOGIN.encode(),b'scram-sha-256')
        preparation.validate_scram_verifier(encrypted)
        with admin.transaction():
            admin.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(
                sql.Identifier(target.LOGIN),sql.Literal(encrypted.decode('ascii'))))
            preparation.validate_registered_metadata(preparation.scope_metadata(admin),login_enabled=True)
        result['credential_rotations']=1
        restricted=dict(parameters,user=target.LOGIN,password=password,dbname=target.DATABASE)
        def revoke():
            raw=connect_exact(connect,parameters,target.DATABASE)
            try:return preparation.set_synthetic_owner_revoked(raw)
            finally:raw.close()
        result['restricted_trial']=prior.trial.verify_trial(
            lambda:connect_exact(connect,restricted,target.DATABASE,restricted=True),
            revoke_authority=revoke,installer_identity=target.ADMIN)
        require(result['restricted_trial']['status']=='passed','resume_restricted_trial_unaccepted')
        result['fresh_restricted_login_verified']=True
        raw=connect_exact(connect,restricted,target.DATABASE,restricted=True)
        try:
            prior._transport(raw,restricted)
            ssl=raw.execute('SELECT ssl,version FROM pg_catalog.pg_stat_ssl WHERE pid=pg_catalog.pg_backend_pid()').fetchone()
            require(type(ssl)is dict and ssl.get('ssl')is True and ssl.get('version') in ('TLSv1.2','TLSv1.3'),
                'resume_positive_tls_unaccepted')
            address=raw.info.hostaddr
            result['positive_tls']=dict(status='passed',sslmode='verify-full',protocol=ssl['version'],ca_path=prior.CA_PATH)
        finally:raw.close()
        result['negative_hostname_tls']=prior._hostname_probe(connect,restricted,address)
        result['provider_probes']=prior._provider_probes(connect,restricted)
        try:result['provider_boundary']=prior.boundary.validate_provider_behavior(result['provider_probes'])
        except prior.boundary.BoundaryError:result['provider_boundary']=dict(status='unaccepted_requires_behavior_review')
        result['status']='restricted_login_trial_passed_additional_acceptance_pending'
        require(password.encode() not in canonical(result) and admin_secret.encode() not in canonical(result),
            'resume_sensitive_output_rejected')
    except Exception as error:
        code=str(error) if isinstance(error,(ValueError,preparation.PreparationError,permissions.PermissionDenied)) else ''
        result['reason']=code if re.fullmatch('(resume|scope|registered|restricted|migration|trusted|public_schema)_[a-z_]+',code) else 'resume_sql_unaccepted'
    finally:
        if admin is not None:admin.close()
    require(len(canonical(result))<8192,'resume_result_oversized')
    return result


def main(admin_secret,migration_text,*,connect,emit=print):
    require(hashlib.sha256(migration_text.encode()).hexdigest()==preparation.MIGRATION_SHA256,
        'resume_migration_source_differs')
    result=run(admin_secret,connect=connect);emit(PREFIX+canonical(result).decode('ascii'),flush=True);return result


def build(source,hashes,runtime):
    import owner_task_isolated_sql_root_launch as root
    paths=set(root.SOURCE_PATHS)|{diagnosis.PATH,PATH}
    require(type(source)is dict and type(hashes)is dict and set(source)==set(hashes)==paths
        and all(type(v)is bytes and hashlib.sha256(v).hexdigest()==hashes[k] for k,v in source.items()),
        'resume_sources_invalid')
    parent_paths=set(root.SOURCE_PATHS)|{diagnosis.PATH}
    base=diagnosis.build({k:source[k] for k in parent_paths},{k:hashes[k] for k in parent_paths},runtime)
    decoded={k:v.decode('utf-8') for k,v in source.items()}
    require(sum(map(len,source.values()))<=512*1024 and '\x00' not in decoded[PATH],'resume_sources_invalid')
    compile(decoded[PATH],PATH,'exec')
    modules=root.SOURCE_MODULES+((diagnosis.PATH,'owner_task_isolated_scope_diagnostic'),(PATH,'owner_task_isolated_sql_safe_encoding'))
    launcher=root.LAUNCHER.replace('sys.modules["run_owner_task_isolated_sql_root"].main',
        'sys.modules["owner_task_isolated_sql_safe_encoding"].main',1).replace(
        'OWNER_TASK_ISOLATED_SQL_ROOT_LAUNCH_FAILURE_V1 ','OWNER_TASK_ISOLATED_SQL_SAFE_ENCODING_LAUNCH_FAILURE_V1 ')
    script=('_sources='+repr(decoded)+'\n_hashes='+repr(hashes)+'\n_modules='+repr(modules)
        +'\n_runtime='+repr(runtime)+'\n_migration='+repr(root.MIGRATION)+'\n'+launcher)
    # Translate the unused colon symbol for dollar signs before transport.
    # Container argument expansion must have nothing it can substitute.
    # Base85 keeps the complete verified 16-source continuation inside the
    # existing 64 KiB argv bound; no source is dropped or weakened to fit.
    packed=base64.b85encode(lzma.compress(script.encode(),preset=9)).decode('ascii').replace(chr(36),':')
    definition=json.loads(canonical(base['definition']))
    definition['tags'].update(purpose='owner-isolated-sql-resume',operation=OPERATION,
        sourceSha256=root.digest(hashes),fullSourceSha256=hashlib.sha256(script.encode()).hexdigest())
    args=['-I','-B','-c',"import base64,lzma;exec(compile(lzma.decompress(base64.b85decode('"+packed+"'.replace(':',chr(36)))),'isolated-sql-safe-encoding','exec'))"]
    require(len(canonical(args))<=65536 and len(JOB)<=32 and chr(36) not in canonical(args).decode(),
        'resume_args_invalid')
    definition['properties']['template']['containers'][0]['args']=args
    return dict(definition=definition,definition_sha256=root.digest(definition),source_hashes=hashes,
        expected_runtime=runtime,step_2_complete=False)

