"""Exact additive installer job definition; no network/secret/SQL on import."""
import base64
import lzma
import hashlib
import json
import re

import owner_task_provider_runtime as runtime
import owner_task_provider_launch_contract as prior
import run_owner_task_isolated_sql as worker
from ibkr_etl import owner_codex_isolated_target as target

SOURCE_MODULES = (
    ('src/ibkr_etl/owner_codex_isolated_target.py', 'ibkr_etl.owner_codex_isolated_target'),
    ('src/ibkr_etl/owner_codex_catalog_transport.py', 'ibkr_etl.owner_codex_catalog_transport'),
    ('src/ibkr_etl/util.py', 'ibkr_etl.util'),
    ('src/ibkr_etl/db.py', 'ibkr_etl.db'),
    ('src/ibkr_etl/owner_codex_tasks.py', 'ibkr_etl.owner_codex_tasks'),
    ('scripts/inspect_owner_task_isolated_provider.py', 'inspect_owner_task_isolated_provider'),
    ('scripts/owner_task_provider_runtime.py', 'owner_task_provider_runtime'),
    ('scripts/run_owner_task_isolated_provider_observation.py', 'run_owner_task_isolated_provider_observation'),
    ('scripts/owner_task_azure_database_boundary.py', 'owner_task_azure_database_boundary'),
    ('scripts/owner_task_isolated_permissions.py', 'owner_task_isolated_permissions'),
    ('scripts/prepare_owner_task_isolated_trial.py', 'prepare_owner_task_isolated_trial'),
    ('scripts/run_owner_task_isolated_trial.py', 'run_owner_task_isolated_trial'),
    ('scripts/run_owner_task_isolated_sql.py', 'run_owner_task_isolated_sql'))
MIGRATION = 'migrations/postgres/059_owner_codex_tasks.sql'
SOURCE_PATHS = tuple(p for p, _ in SOURCE_MODULES) + (MIGRATION,)
CONTAINER = 'isolated-sql-trial'
canonical = prior.canonical
digest = prior.digest
require = prior.require

LAUNCHER = '''import hashlib,json,os,sys,types
try:
    if any(k.upper().startswith("PG") or k.upper() in ("SSL_CERT_FILE","SSL_CERT_DIR","OPENSSL_CONF","OPENSSL_MODULES","IBKR_PROJECT_ROOT") for k in os.environ):
        raise ValueError("isolated_sql_ambient_override")
    if set(_sources)!=set(_hashes) or any(hashlib.sha256(_sources[k].encode()).hexdigest()!=_hashes[k] for k in _sources):
        raise ValueError("isolated_sql_source_mismatch")
    _package=types.ModuleType("ibkr_etl");_package.__path__=[];sys.modules["ibkr_etl"]=_package
    for _path,_name in _modules:
        _module=types.ModuleType(_name);_module.__file__=_path
        _module.__package__="ibkr_etl" if _name.startswith("ibkr_etl.") else ""
        sys.modules[_name]=_module
        if _name.startswith("ibkr_etl."): setattr(_package,_name.rsplit(".",1)[1],_module)
        exec(compile(_sources[_path],_path,"exec"),_module.__dict__)
    if sys.modules["owner_task_provider_runtime"].check_runtime()!=_runtime:
        raise ValueError("isolated_sql_runtime_mismatch")
    import psycopg
    _secret=os.environ.pop("OWNERTRIAL_ADMIN_URL",None)
    if type(_secret) is not str or not _secret: raise ValueError("isolated_sql_secret_missing")
    _result=sys.modules["run_owner_task_isolated_sql"].main(_secret,_sources[_migration],connect=psycopg.connect)
    _secret=None
    if _result["status"]=="unaccepted": raise SystemExit(1)
except SystemExit:
    raise
except BaseException:
    print("OWNER_TASK_ISOLATED_SQL_LAUNCH_FAILED_V1",flush=True)
    raise SystemExit(1) from None
'''


def build(source, hashes, expected_runtime):
    require(type(source) is dict and type(hashes) is dict and set(source) == set(hashes) == set(SOURCE_PATHS),
        'isolated_sql_launch_source_invalid')
    runtime.validate_descriptor(expected_runtime)
    require(sum(len(value) for value in source.values()) <= 512 * 1024,
        'isolated_sql_launch_source_invalid')
    decoded = {}
    for path in SOURCE_PATHS:
        require(type(source[path]) is bytes and type(hashes[path]) is str
            and re.fullmatch('[0-9a-f]{64}', hashes[path])
            and hashlib.sha256(source[path]).hexdigest() == hashes[path], 'isolated_sql_launch_source_invalid')
        decoded[path] = source[path].decode('utf-8')
        require('\x00' not in decoded[path], 'isolated_sql_launch_source_invalid')
        if path.endswith('.py'): compile(decoded[path], path, 'exec')
    require(hashes[MIGRATION] == worker.preparation.MIGRATION_SHA256, 'isolated_sql_launch_migration_invalid')
    script = ('_sources=' + repr(decoded) + '\n_hashes=' + repr(hashes) + '\n_modules=' + repr(SOURCE_MODULES)
        + '\n_runtime=' + repr(expected_runtime) + '\n_migration=' + repr(MIGRATION) + '\n' + LAUNCHER)
    packed = base64.b64encode(lzma.compress(script.encode(), preset=9)).decode('ascii')
    args = ['-I', '-B', '-c', "import base64,lzma;exec(compile(lzma.decompress(base64.b64decode('"
        + packed + "')),'isolated-sql-trial-launch','exec'))"]
    require(len(canonical(args)) <= 65536, 'isolated_sql_launch_args_oversized')
    definition = dict(location=target.LOCATION,
        identity=dict(type='UserAssigned', userAssignedIdentities={worker.IDENTITY_ID: {}}),
        tags=dict(purpose='owner-isolated-sql-trial', operation=worker.OPERATION,
            profile=target.PROFILE_KEY, sourceSha256=digest(hashes), fullSourceSha256=hashlib.sha256(script.encode()).hexdigest()),
        properties=dict(environmentId=target.ENVIRONMENT,
            configuration=dict(triggerType='Manual', replicaTimeout=600, replicaRetryLimit=0,
                identitySettings=[dict(identity=worker.IDENTITY_ID, lifecycle='None')],
                manualTriggerConfig=dict(parallelism=1, replicaCompletionCount=1),
                secrets=[dict(name=target.SECRET_NAME, keyVaultUrl=prior.SECRET_VERSION_URL, identity=worker.IDENTITY_ID)],
                registries=[dict(server=prior.REGISTRY_SERVER, identity=worker.IDENTITY_ID)]),
            template=dict(containers=[dict(name=CONTAINER, image=prior.IMAGE, command=['python'], args=args,
                env=[dict(name='OWNERTRIAL_ADMIN_URL', secretRef=target.SECRET_NAME)],
                resources=dict(cpu=0.5, memory='1Gi'))])))
    return dict(definition=definition, definition_sha256=digest(definition), source_hashes=dict(hashes),
        expected_runtime=json.loads(canonical(expected_runtime)), sql_installation_dispatched=False,
        model_credential_isolation_verified=False, step_2_complete=False)
