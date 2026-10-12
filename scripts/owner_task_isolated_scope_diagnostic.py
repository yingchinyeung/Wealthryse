"""Read-only diagnosis of the prepared private fixture; NOT a fresh-login pass.

No import-time IO. This never rotates a credential, recreates a database,
changes data/ACLs, invokes a model, or relaxes the frozen acceptance harness.
Only the trusted process receives the existing private-test admin credential.
"""
import base64
import hashlib
import json
import lzma
import re

from ibkr_etl import owner_codex_isolated_target as target
import run_owner_task_isolated_sql_root as prior_worker
import prepare_owner_task_isolated_trial as preparation
import owner_task_isolated_permissions as permissions

OPERATION = '16940f5d-a442-40e1-ab49-d587803f9a7b'
JOB = 'ibkrv2stg-ownscope-20261005'
JOB_ID = target.BASE + 'Microsoft.App/jobs/' + JOB
PREFIX = 'OWNER_TASK_ISOLATED_SCOPE_DIAGNOSTIC_V1 '
PATH = 'scripts/owner_task_isolated_scope_diagnostic.py'
OLD_PATH = target.SCHEMA + ',pg_catalog'
EXACT_PATH = target.SCHEMA + ', pg_catalog'
IDENTITY_SQL = """SELECT current_database()::text AS database,current_user::text AS role,
session_user::text AS login,current_setting('search_path') AS search_path,
current_schemas(false)::text[] AS effective_schemas,
current_setting('app.tenant_id',true) AS tenant_scope,
current_setting('app.owner_object_id',true) AS owner_scope,
pg_my_temp_schema() AS temp_schema"""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def require(value, code):
    if not value: raise ValueError(code)


def validate_identity(value, search_path):
    require(type(value) is dict and set(value) == {'database','role','login','search_path',
        'effective_schemas','tenant_scope','owner_scope','temp_schema'}
        and value['database'] == target.DATABASE and value['role'] == target.SCHEMA_OWNER
        and value['login'] == target.ADMIN and value['search_path'] == search_path
        and value['effective_schemas'] == [target.SCHEMA, 'pg_catalog']
        and value['tenant_scope'] in (None,'') and value['owner_scope'] in (None,'')
        and type(value['temp_schema']) is int and value['temp_schema'] == 0,
        'scope_diagnostic_identity_differs')


def run(admin_secret, *, connect):
    result = dict(status='unaccepted', operation_id=OPERATION, job_resource_id=JOB_ID,
        host=target.HOST, database=target.DATABASE, source_rows_read=0, source_rows_written=0,
        data_changes=0, credential_rotations=0, grants_changed=0, model_calls=0, agents_activated=0,
        fresh_restricted_login_verified=False, model_credential_isolation_verified=False,
        step_2_complete=False, automatic_retry=False)
    raw = None
    try:
        parameters = prior_worker.parser.validate_admin_secret(admin_secret)
        prior_worker.parser.require_ca_bundle()
        raw = prior_worker._connect(connect, parameters, target.DATABASE)
        prior_worker._transport(raw, dict(parameters, dbname=target.DATABASE))
        preparation.bind_trusted_connection(raw, target.DATABASE)
        preparation.validate_registered_metadata(preparation.scope_metadata(raw), login_enabled=True)
        result['permission_manifest'] = permissions.verify_permissions(raw, permissions.PermissionTarget(),
            installer_identity=target.ADMIN, require_fresh_login=False, login_enabled=True)
        # Session settings only. Admin authentication is explicitly retained;
        # The admin has SET membership only in the fixture schema owner, not
        # the runtime capability. Do not grant extra membership for diagnosis.
        # SET ROLE is not used to pretend that this is a fresh restricted login.
        raw.execute('SET default_transaction_read_only=on')
        raw.execute('SET ROLE ' + target.SCHEMA_OWNER)
        observed = []
        for path in (OLD_PATH, EXACT_PATH):
            raw.execute("SELECT pg_catalog.set_config('search_path',%s,false)", (path,))
            identity = raw.execute(IDENTITY_SQL).fetchone()
            validate_identity(identity, path); observed.append(identity)
        result.update(status='readonly_scope_diagnostic_completed', observations=observed,
            same_effective_schema_order=True, exact_harness_string_matches=[False, True],
            startup_option_format_mismatch_reproduced=True,
            restricted_login_authentication_not_retested=True)
        require(admin_secret.encode() not in canonical(result), 'scope_diagnostic_sensitive_output')
    except Exception as error:
        code = str(error) if isinstance(error, (ValueError, permissions.PermissionDenied)) else ''
        result['reason'] = code if re.fullmatch('[a-z][a-z0-9_]{0,79}',code) else 'scope_diagnostic_unaccepted'
    finally:
        if raw is not None: raw.close()
    require(len(canonical(result)) < 8192, 'scope_diagnostic_result_oversized')
    return result


def main(admin_secret, migration_text, *, connect, emit=print):
    # Preserve the existing launcher interface; no migration is executed here.
    require(hashlib.sha256(migration_text.encode()).hexdigest() == preparation.MIGRATION_SHA256,
        'scope_diagnostic_migration_source_differs')
    result = run(admin_secret, connect=connect)
    emit(PREFIX + canonical(result).decode('ascii'), flush=True)
    return result


def build(source, hashes, runtime):
    """New physical read-only artifact. Never mutate frozen launcher globals."""
    import owner_task_isolated_sql_root_launch as prior
    paths = set(prior.SOURCE_PATHS) | {PATH}
    require(type(source) is dict and type(hashes) is dict and set(source) == set(hashes) == paths,
        'scope_diagnostic_sources_invalid')
    require(all(type(v) is bytes and hashlib.sha256(v).hexdigest() == hashes[k]
        for k,v in source.items()), 'scope_diagnostic_sources_invalid')
    base = prior.build({k:source[k] for k in prior.SOURCE_PATHS},
        {k:hashes[k] for k in prior.SOURCE_PATHS},runtime)
    decoded = {k:v.decode('utf-8') for k,v in source.items()}
    require(sum(len(v) for v in source.values()) <= 512*1024 and '\x00' not in decoded[PATH],
        'scope_diagnostic_sources_invalid')
    compile(decoded[PATH],PATH,'exec')
    modules = prior.SOURCE_MODULES + ((PATH,'owner_task_isolated_scope_diagnostic'),)
    launcher = prior.LAUNCHER.replace('sys.modules["run_owner_task_isolated_sql_root"].main',
        'sys.modules["owner_task_isolated_scope_diagnostic"].main',1)
    launcher = launcher.replace('OWNER_TASK_ISOLATED_SQL_ROOT_LAUNCH_FAILURE_V1 ',
        'OWNER_TASK_ISOLATED_SCOPE_LAUNCH_FAILURE_V1 ')
    script = ('_sources='+repr(decoded)+'\n_hashes='+repr(hashes)+'\n_modules='+repr(modules)
        +'\n_runtime='+repr(runtime)+'\n_migration='+repr(prior.MIGRATION)+'\n'+launcher)
    packed=base64.b64encode(lzma.compress(script.encode(),preset=9)).decode('ascii')
    definition=json.loads(canonical(base['definition']))
    definition['tags'].update(purpose='owner-isolated-scope-readonly',operation=OPERATION,
        sourceSha256=prior.digest(hashes),fullSourceSha256=hashlib.sha256(script.encode()).hexdigest())
    args=['-I','-B','-c',"import base64,lzma;exec(compile(lzma.decompress(base64.b64decode('"+packed+"')),'isolated-scope-diagnostic','exec'))"]
    require(len(canonical(args)) <= 65536 and len(JOB) <= 32,'scope_diagnostic_args_invalid')
    definition['properties']['template']['containers'][0]['args']=args
    return dict(definition=definition,definition_sha256=prior.digest(definition),source_hashes=hashes,
        expected_runtime=runtime,step_2_complete=False)
