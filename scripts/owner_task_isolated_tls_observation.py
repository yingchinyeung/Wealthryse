"""Fresh TLS/provider observation only; do not replay passed synthetic CRUD.

One disposable LOGIN password rotation follows immutable-fixture/ACL review.
TLS is measured as the authenticated LOGIN, not a role unable to inspect its
session statistics. No monitoring grant, fixture reset, source data or model.
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
import owner_task_isolated_permissions as permissions
import prepare_owner_task_isolated_trial as preparation

OPERATION='c0f4ac37-55b6-4fa8-9a58-7fcb7e48c31d'
JOB='ibkrv2stg-owntls-20261005'
JOB_ID=target.BASE+'Microsoft.App/jobs/'+JOB
PREFIX='OWNER_TASK_ISOLATED_TLS_OBSERVATION_V1 '
PATH='scripts/owner_task_isolated_tls_observation.py'
canonical=prior.canonical
require=prior.require


def connect_exact(connect,parameters,database):
    require(type(parameters)is dict and parameters.get('host')==target.HOST
        and parameters.get('user') in (target.ADMIN,target.LOGIN)
        and parameters.get('sslmode')=='verify-full' and parameters.get('sslrootcert')==prior.CA_PATH
        and database in {target.DATABASE}|prior.boundary.PROVIDER_DATABASES,
        'isolated_sql_observation_connection_invalid')
    return connect(**dict(parameters,dbname=database),connect_timeout=20,autocommit=True,
        row_factory=dict_row,options='-c search_path=pg_catalog -c statement_timeout=45000 -c lock_timeout=3000 -c idle_in_transaction_session_timeout=45000',
        application_name='ownertrial-isolated-tls-observation')


def fixture_fingerprint(raw):
    preparation.bind_trusted_connection(raw,target.DATABASE)
    preparation.validate_registered_metadata(preparation.scope_metadata(raw),login_enabled=True)
    manifest=permissions.verify_permissions(raw,permissions.PermissionTarget(),
        installer_identity=target.ADMIN,require_fresh_login=False,login_enabled=True)
    p=preparation
    owners=((p.TENANT_A,p.OWNER_A),(p.TENANT_A,p.OWNER_B),(p.TENANT_B,p.OWNER_A))
    state=[]
    with raw.transaction():
        raw.execute('SET TRANSACTION READ ONLY')
        raw.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(target.SCHEMA_OWNER)))
        authority=raw.execute(sql.SQL('SELECT tenant_id::text AS tenant_id,owner_object_id::text AS owner_object_id,active,role_name,registered_subject_ids,authorized_subject_ids FROM {}.owner_task_authority_fixture ORDER BY tenant_id,owner_object_id').format(sql.Identifier(target.SCHEMA))).fetchall()
        expected=[dict(tenant_id=t,owner_object_id=o,active=(t,o)!=(p.TENANT_A,p.OWNER_A),role_name='owner',registered_subject_ids=['SYNTH_A','SYNTH_B'],authorized_subject_ids=['SYNTH_A','SYNTH_B']) for t,o in owners]
        require(authority==expected,'isolated_sql_post_trial_authority_changed')
        for tenant,owner in owners:
            raw.execute("SELECT pg_catalog.set_config('app.tenant_id',%s,true)",(tenant,))
            raw.execute("SELECT pg_catalog.set_config('app.owner_object_id',%s,true)",(owner,))
            for table in permissions.TASK_TABLES:
                # Read ONLY registered synthetic scopes. Order by the complete
                # serialized row; limit+one rejects excess instead of truncating.
                rows=raw.execute(sql.SQL('SELECT pg_catalog.row_to_json(t)::text AS serialized FROM {}.{} t ORDER BY serialized LIMIT 129').format(sql.Identifier(target.SCHEMA),sql.Identifier(table))).fetchall()
                require(len(rows)<=128 and all(type(row)is dict and set(row)=={'serialized'} and type(row['serialized'])is str for row in rows),'isolated_sql_fixture_fingerprint_invalid')
                state.append(dict(tenant=tenant,owner=owner,table=table,row_count=len(rows),rows_sha256=hashlib.sha256(canonical(rows)).hexdigest()))
    return dict(permission_manifest_sha256=manifest['permission_manifest_sha256'],
        registered_scope_state_sha256=hashlib.sha256(canonical(state)).hexdigest(),
        registered_owner_scopes_verified=3,all_unknown_owner_scopes_claimed_verified=False)


def own_login_tls(raw,parameters):
    prior._transport(raw,dict(parameters,dbname=target.DATABASE))
    identity=raw.execute('SELECT session_user::text AS login,current_user::text AS role,current_database() AS database').fetchone()
    require(identity==dict(login=target.LOGIN,role=target.LOGIN,database=target.DATABASE),
        'isolated_sql_tls_login_identity_invalid')
    own=raw.execute('SELECT ssl,version FROM pg_catalog.pg_stat_ssl WHERE pid=pg_catalog.pg_backend_pid()').fetchone()
    require(type(own)is dict and set(own)=={'ssl','version'} and own['ssl']is True and own['version'] in ('TLSv1.2','TLSv1.3'),
        'isolated_sql_own_login_tls_unaccepted')
    with raw.transaction():
        raw.execute('SET TRANSACTION READ ONLY')
        raw.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(target.CAPABILITY)))
        masked=raw.execute('SELECT ssl,version FROM pg_catalog.pg_stat_ssl WHERE pid=pg_catalog.pg_backend_pid()').fetchone()
        require(type(masked)is dict and set(masked)=={'ssl','version'} and
            (masked==dict(ssl=None,version=None) or masked==own),'isolated_sql_tls_role_observation_invalid')
    restored=raw.execute('SELECT current_user::text AS role').fetchone()
    require(restored=={'role':target.LOGIN},'isolated_sql_tls_role_not_restored')
    return dict(status='passed',sslmode='verify-full',protocol=own['version'],ca_path=prior.CA_PATH,
        fresh_authenticated_login=target.LOGIN,own_login_statistics=own,
        capability_statistics=masked,capability_statistics_masked=(masked==dict(ssl=None,version=None)),
        monitoring_privileges_added=False)


def provider_observations(connect,parameters):
    probes=[];probe_diagnostics=[]
    for database in sorted(prior.boundary.PROVIDER_DATABASES):
        raw=None
        probe=dict(host=target.HOST,database=database,login=target.LOGIN,
            credential_target_login_verified=True,fresh_connection=True,
            outcome='unclassified_failure',sqlstate='',source_rows_read=0,source_rows_written=0)
        diagnostic=dict(database=database,classification='unclassified',generic_failure_accepted=False)
        try:
            raw=connect_exact(connect,parameters,database)
            prior._transport(raw,dict(parameters,dbname=database))
            identity=raw.execute('SELECT session_user::text AS login,current_database() AS database').fetchone()
            require(identity==dict(login=target.LOGIN,database=database),'isolated_sql_provider_identity_invalid')
            probe['outcome']='connected_requires_object_review'
            diagnostic['classification']='connected_no_object_values_read'
        except Exception as error:
            state=getattr(error,'sqlstate',None)
            if type(state)is str and re.fullmatch('[0-9A-Z]{5}',state):
                probe['sqlstate']=state
                if state in ('42501','28000'):probe['outcome']='authorization_denied'
            # Classify only exact server diagnostic fragments. Never retain
            # DSNs, credential-bearing exception text or invented SQLSTATEs.
            message=str(error)
            if 'FATAL:  permission denied for database "'+database+'"' in message:
                diagnostic['classification']='exact_database_permission_denied_text'
            elif 'FATAL:  database "'+database+'" is not currently accepting connections' in message:
                diagnostic['classification']='exact_database_not_accepting_connections_text'
            diagnostic['sqlstate_available']=bool(probe['sqlstate'])
        finally:
            if raw is not None:raw.close()
        probes.append(probe);probe_diagnostics.append(diagnostic)
    return probes,probe_diagnostics


def run(admin_secret,*,connect):
    result=dict(status='unaccepted',operation_id=OPERATION,job_resource_id=JOB_ID,host=target.HOST,
        database=target.DATABASE,source_rows_read=0,source_rows_written=0,production_changed=False,
        roles_created=0,grants_changed=0,database_recreated=False,credential_rotations=0,
        fixture_rows_written=0,restricted_crud_repeated=False,model_calls=0,agents_activated=0,
        model_credential_isolation_verified=False,step_2_complete=False,automatic_retry=False)
    admin=None
    try:
        parameters=prior.parser.validate_admin_secret(admin_secret);prior.parser.require_ca_bundle()
        admin=connect_exact(connect,parameters,target.DATABASE);prior._transport(admin,dict(parameters,dbname=target.DATABASE))
        before=fixture_fingerprint(admin);result['fixture_before']=before
        password=secrets.token_urlsafe(48)
        require(re.fullmatch('[A-Za-z0-9_-]{48,128}',password),'isolated_sql_password_shape_invalid')
        encrypted=admin.pgconn.encrypt_password(password.encode(),target.LOGIN.encode(),b'scram-sha-256')
        preparation.validate_scram_verifier(encrypted)
        with admin.transaction():
            admin.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(sql.Identifier(target.LOGIN),sql.Literal(encrypted.decode('ascii'))))
            preparation.validate_registered_metadata(preparation.scope_metadata(admin),login_enabled=True)
        result['credential_rotations']=1
        restricted=dict(parameters,user=target.LOGIN,password=password,dbname=target.DATABASE)
        raw=connect_exact(connect,restricted,target.DATABASE)
        try:
            result['positive_tls']=own_login_tls(raw,restricted);address=raw.info.hostaddr
        finally:raw.close()
        result['negative_hostname_tls']=prior._hostname_probe(connect,restricted,address)
        probes,diagnostics=provider_observations(connect,restricted)
        result.update(provider_probes=probes,provider_diagnostics=diagnostics)
        try:result['provider_boundary']=prior.boundary.validate_provider_behavior(probes)
        except prior.boundary.BoundaryError:result['provider_boundary']=dict(status='unaccepted_requires_behavior_review')
        after=fixture_fingerprint(admin);result['fixture_after']=after
        require(before==after,'isolated_sql_registered_fixture_changed')
        result['fixture_unchanged']=True
        result['status']='tls_observation_completed_additional_acceptance_pending'
        require(password.encode() not in canonical(result) and admin_secret.encode() not in canonical(result),'isolated_sql_sensitive_output_rejected')
    except Exception as error:
        code=str(error) if isinstance(error,(ValueError,preparation.PreparationError,permissions.PermissionDenied)) else ''
        result['reason']=code if re.fullmatch('(isolated_sql|scope|registered|restricted|migration|trusted|public_schema)_[a-z_]+',code) else 'isolated_sql_tls_observation_unaccepted'
    finally:
        if admin is not None:admin.close()
    require(len(canonical(result))<8192,'isolated_sql_result_oversized')
    return result


def main(admin_secret,migration_text,*,connect,emit=print):
    require(hashlib.sha256(migration_text.encode()).hexdigest()==preparation.MIGRATION_SHA256,'isolated_sql_migration_differs')
    result=run(admin_secret,connect=connect);emit(PREFIX+canonical(result).decode('ascii'),flush=True);return result


def build(source,hashes,runtime):
    import owner_task_isolated_sql_root_launch as root
    paths=set(root.SOURCE_PATHS)|{PATH}
    require(type(source)is dict and type(hashes)is dict and set(source)==set(hashes)==paths
        and all(type(v)is bytes and hashlib.sha256(v).hexdigest()==hashes[k] for k,v in source.items()),'isolated_sql_observation_sources_invalid')
    base=root.build({k:source[k] for k in root.SOURCE_PATHS},{k:hashes[k] for k in root.SOURCE_PATHS},runtime)
    decoded={k:v.decode('utf-8') for k,v in source.items()}
    for path,text in decoded.items():
        if path.endswith('.py'):compile(text,path,'exec')
    modules=root.SOURCE_MODULES+((PATH,'owner_task_isolated_tls_observation'),)
    launcher=root.LAUNCHER.replace('sys.modules["run_owner_task_isolated_sql_root"].main','sys.modules["owner_task_isolated_tls_observation"].main',1).replace('OWNER_TASK_ISOLATED_SQL_ROOT_LAUNCH_FAILURE_V1 ','OWNER_TASK_ISOLATED_TLS_OBSERVATION_LAUNCH_FAILURE_V1 ')
    script=('_sources='+repr(decoded)+'\n_hashes='+repr(hashes)+'\n_modules='+repr(modules)+'\n_runtime='+repr(runtime)+'\n_migration='+repr(root.MIGRATION)+'\n'+launcher)
    packed=base64.b85encode(lzma.compress(script.encode(),preset=9)).decode('ascii').replace(chr(36),':')
    definition=json.loads(canonical(base['definition']))
    definition['tags'].update(purpose='owner-isolated-tls-observation',operation=OPERATION,sourceSha256=root.digest(hashes),fullSourceSha256=hashlib.sha256(script.encode()).hexdigest())
    args=['-I','-B','-c',"import base64,lzma;exec(compile(lzma.decompress(base64.b85decode('"+packed+"'.replace(':',chr(36)))),'isolated-tls-observation','exec'))"]
    require(len(canonical(args))<=65536 and len(JOB)<=32 and chr(36) not in canonical(args).decode(),'isolated_sql_observation_args_invalid')
    definition['properties']['template']['containers'][0]['args']=args
    return dict(definition=definition,definition_sha256=root.digest(definition),source_hashes=hashes,expected_runtime=runtime,step_2_complete=False)
