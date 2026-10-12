"""Separately named synthetic SQL worker after a rejected 33-character job name.

Not a model tool. The external controller must protect exact intent/source and
reserve one dispatch first. Passwords remain inside this trusted test process;
this trial deliberately does NOT claim model/process credential isolation.
No source data, production SQL, cleanup, retries or agent activation occurs.
"""
import hashlib
import ipaddress
import json
import re
import secrets

from psycopg.rows import dict_row

from ibkr_etl import owner_codex_isolated_target as target
import owner_task_azure_database_boundary as boundary
import prepare_owner_task_isolated_trial as preparation
import run_owner_task_isolated_trial as trial
import run_owner_task_isolated_provider_observation as parser

OPERATION = '47fffc91-5697-45e0-b736-84d8d123158b'
JOB = 'ibkrv2stg-ownsql-20261005'
JOB_ID = target.BASE + 'Microsoft.App/jobs/' + JOB
IDENTITY = 'ibkrv2stg-ownertrial-sql-identity'
IDENTITY_ID = target.BASE + 'Microsoft.ManagedIdentity/userAssignedIdentities/' + IDENTITY
PREFIX = 'OWNER_TASK_ISOLATED_SQL_NAMED_V1 '
MAX_BYTES = 8192
CA_PATH = parser.CA_PATH


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def require(value, code):
    if not value: raise ValueError(code)


def _connect(connect, parameters, database, *, restricted=False):
    require(database in {'postgres', target.DATABASE} | boundary.PROVIDER_DATABASES,
        'isolated_sql_database_unregistered')
    options = '-c search_path=pg_catalog -c statement_timeout=45000 -c lock_timeout=3000 -c idle_in_transaction_session_timeout=45000'
    if restricted and database == target.DATABASE:
        options += ' -c role=' + target.CAPABILITY + ' -c search_path=' + target.SCHEMA + ',pg_catalog'
    return connect(**dict(parameters, dbname=database), connect_timeout=20, autocommit=True,
        row_factory=dict_row, options=options, application_name='ownertrial-isolated-sql')


def _transport(raw, parameters):
    expected = {k: str(parameters[k]) for k in ('host', 'user', 'dbname', 'port', 'sslmode', 'sslrootcert')}
    observed = {}
    for item in raw.pgconn.info:
        if item.keyword in {k.encode() for k in expected}:
            key = item.keyword.decode('ascii')
            require(key not in observed and type(item.val) is bytes, 'isolated_sql_transport_invalid')
            observed[key] = item.val.decode('ascii')
    require(observed == expected and raw.pgconn.ssl_in_use is True, 'isolated_sql_transport_invalid')


def _provider_probes(connect, parameters):
    probes = []
    for database in sorted(boundary.PROVIDER_DATABASES):
        raw = None
        probe = dict(host=target.HOST, database=database, login=target.LOGIN,
            credential_target_login_verified=True, fresh_connection=True,
            outcome='unclassified_failure', sqlstate='', source_rows_read=0, source_rows_written=0)
        try:
            raw = _connect(connect, parameters, database, restricted=True)
            _transport(raw, dict(parameters, dbname=database))
            identity = raw.execute('SELECT session_user::text AS login,current_database() AS database').fetchone()
            require(identity == dict(login=target.LOGIN, database=database), 'isolated_sql_provider_identity_invalid')
            # Never query Query Store, customer values or monitoring payloads.
            probe['outcome'] = 'connected_requires_object_review'
        except Exception as error:
            state = getattr(error, 'sqlstate', None)
            if type(state) is str and re.fullmatch('[0-9A-Z]{5}', state):
                probe['sqlstate'] = state
                if state in ('42501', '28000'): probe['outcome'] = 'authorization_denied'
        finally:
            if raw is not None: raw.close()
        probes.append(probe)
    return probes


def _hostname_probe(connect, parameters, hostaddr):
    """Use the SAME live server IP; a DNS/network error cannot pass this test."""
    require(type(hostaddr) is str and ipaddress.ip_address(hostaddr).is_private,
        'isolated_sql_host_address_invalid')
    raw = None
    try:
        raw = connect(**dict(parameters, host='ownertrial-invalid.example.test', hostaddr=hostaddr),
            autocommit=True, connect_timeout=20, row_factory=dict_row)
    except Exception as error:
        # libpq connection errors may lack SQLSTATE. Match only certificate
        # hostname verification diagnostics; retain neither raw text nor DSN.
        message = str(error)
        return dict(status='passed' if 'does not match host name' in message
            and 'ownertrial-invalid.example.test' in message else 'unaccepted',
            probe='same_server_wrong_certificate_hostname', sslmode='verify-full',
            generic_network_or_authentication_failure_accepted=False)
    finally:
        if raw is not None: raw.close()
    return dict(status='unaccepted', probe='same_server_wrong_certificate_hostname', sslmode='verify-full',
        generic_network_or_authentication_failure_accepted=False)


def run(admin_secret, migration_text, *, connect):
    result = dict(status='unaccepted', operation_id=OPERATION, host=target.HOST,
        database=target.DATABASE, job_resource_id=JOB_ID, installation_attempted=False,
        fresh_restricted_login_verified=False, model_credential_isolation_verified=False,
        step_2_complete=False, source_rows_read=0, source_rows_written=0,
        production_changed=False, agents_activated=0, model_calls=0, automatic_retry=False)
    admin = None
    try:
        require(callable(connect), 'isolated_sql_connector_missing')
        parameters = parser.validate_admin_secret(admin_secret)
        parser.require_ca_bundle()
        password = secrets.token_urlsafe(48)
        preparation.validate_inputs(migration_text, password)
        admin = _connect(connect, parameters, 'postgres')
        _transport(admin, parameters)
        result['installation_attempted'] = True
        result['preparation'] = preparation.prepare_trial(admin,
            lambda: _connect(connect, parameters, target.DATABASE),
            migration_text=migration_text, password=password)
        restricted = dict(parameters, user=target.LOGIN, password=password, dbname=target.DATABASE)
        def revoke():
            raw = _connect(connect, parameters, target.DATABASE)
            try: return preparation.set_synthetic_owner_revoked(raw)
            finally: raw.close()
        result['restricted_trial'] = trial.verify_trial(
            lambda: _connect(connect, restricted, target.DATABASE, restricted=True),
            revoke_authority=revoke, installer_identity=target.ADMIN)
        require(result['restricted_trial']['status'] == 'passed', 'isolated_sql_restricted_trial_unaccepted')
        result['fresh_restricted_login_verified'] = True
        raw = _connect(connect, restricted, target.DATABASE, restricted=True)
        try:
            _transport(raw, restricted)
            address = raw.info.hostaddr
            ssl = raw.execute('SELECT ssl,version FROM pg_catalog.pg_stat_ssl WHERE pid=pg_catalog.pg_backend_pid()').fetchone()
            require(type(ssl) is dict and ssl.get('ssl') is True and ssl.get('version') in ('TLSv1.2', 'TLSv1.3'),
                'isolated_sql_tls_session_unaccepted')
            result['positive_tls'] = dict(status='passed', sslmode='verify-full', protocol=ssl['version'], ca_path=CA_PATH)
        finally: raw.close()
        result['negative_hostname_tls'] = _hostname_probe(connect, restricted, address)
        result['provider_probes'] = _provider_probes(connect, restricted)
        try:
            result['provider_boundary'] = boundary.validate_provider_behavior(result['provider_probes'])
        except boundary.BoundaryError:
            result['provider_boundary'] = dict(status='unaccepted_requires_behavior_review')
        result['status'] = 'restricted_login_trial_passed_additional_acceptance_pending'
        require(password.encode() not in canonical(result) and admin_secret.encode() not in canonical(result),
            'isolated_sql_sensitive_output_rejected')
    except Exception as error:
        code = str(error) if isinstance(error, (preparation.PreparationError, ValueError)) else ''
        result['reason'] = code if re.fullmatch('(isolated_sql|preparation|registered|migration|restricted|trusted|public_schema)_[a-z_]+', code) else 'isolated_sql_trial_unaccepted'
        state = getattr(error, 'sqlstate', None)
        if type(state) is str and re.fullmatch('[0-9A-Z]{5}', state): result['sqlstate'] = state
    finally:
        if admin is not None: admin.close()
    require(len(canonical(result)) < MAX_BYTES, 'isolated_sql_result_oversized')
    return result


def main(admin_secret, migration_text, *, connect, emit=print):
    result = run(admin_secret, migration_text, connect=connect)
    emit(PREFIX + canonical(result).decode('ascii'), flush=True)
    return result
