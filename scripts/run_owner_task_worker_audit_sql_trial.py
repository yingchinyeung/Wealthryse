"""One isolated SQL audit qualification; no model or real archive receipt.

Only a NEW synthetic database and five roles on the registered private test host
are installed. Separate real writer/reader logins retain no secret in evidence.
This accepts SQL boundaries only, never independent Azure archive identities.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import re
import secrets

from psycopg import sql
from psycopg.rows import dict_row
from ibkr_etl import owner_codex_isolated_target as host
from ibkr_etl.owner_codex_tasks import OwnerRef, ServerValidatedTaskRequest
from ibkr_etl.owner_codex_worker import ReviewWork, WorkerEvidence, _digest
from ibkr_etl.owner_codex_worker_audit import ProducerRegistration, VerificationChallenge, envelope, challenge_scope
import run_owner_task_isolated_provider_observation as credential_parser

OPERATION = '68583794-6450-4a39-ad1e-95b809c75ee8'
JOB = 'ibkrv2stg-ownaudsql-20261005'
JOB_ID = host.BASE + 'Microsoft.App/jobs/' + JOB
DATABASE = 'owner_codex_audit_sql_20261005'
SCHEMA = 'owner_codex_audit_sql_trial'
OWNER = 'ibkr_owner_audit_schema_20261005'
CAPABILITY = 'ibkr_owner_audit_writer_20261005'
LOGIN = 'ibkr_owner_audit_wlogin_20261005'
READER_CAP = 'ibkr_owner_audit_reader_20261005'
READER_LOGIN = 'ibkr_owner_audit_rlogin_20261005'
ROLES = (OWNER, CAPABILITY, LOGIN, READER_CAP, READER_LOGIN)
MARKER = 'owner-codex-audit-sql-isolated-20261005-v1'
TABLE = 'owner_codex_worker_audit_events'
PATH = 'scripts/run_owner_task_worker_audit_sql_trial.py'
DDL_PATH = 'scripts/owner_task_worker_audit_schema.sql'
DDL_SHA = '885c892dc01eb1004d41e8e4440e51f761eb709a140a2eb92e394a53d094468f'
CONSTRAINT_PATH = DDL_PATH
PREFIX = 'OWNER_TASK_WORKER_AUDIT_SQL_TRIAL_V1 '
TENANT = '00000000-0000-0000-0000-000000000001'
WORKER = '00000000-0000-0000-0000-000000000701'
PRINCIPAL = '00000000-0000-0000-0000-000000000702'
CHECKS = ('two_restricted_logins_tls_and_acl','committed_writer_independent_sql_read',
    'read_only_and_append_only_permission_denials','tenant_worker_identity_bound_rls',
    'logical_phase_duplicate_consumption','lost_acknowledgement_consumption',
    'fresh_reader_login_revocation','predecessor_catalog_unchanged')


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()


def require(value, code='fence_audit_sql_unverified'):
    if not value:
        raise ValueError(code)


def uid(number):
    return f'00000000-0000-0000-0000-{number:012d}'


def work(number):
    return ReviewWork(OwnerRef(TENANT,uid(704)),uid(10000+number),uid(20000+number),
        WORKER,uid(30000+number),1,'b'*64,ServerValidatedTaskRequest(('SYNTH_AUDIT_SQL',),'a'*64))


def evidence(number):
    return WorkerEvidence(work(number),'intent',None,None,'not_started',False,'c'*64,'d'*64,PRINCIPAL)


def payload(number):
    item=evidence(number)
    registration=ProducerRegistration(TENANT,WORKER,PRINCIPAL,'e'*64,'f'*64,uid(705),uid(706))
    # Synthetic registration/challenge, explicitly NOT Azure identity acceptance.
    return item,envelope(item,registration,VerificationChallenge(uid(40000+number),challenge_scope(item)))


def connect_exact(connect, parameters, database, *, reader=None):
    require(database in ('postgres',DATABASE),'fence_audit_database_unregistered')
    expected=host.ADMIN if reader is None else READER_LOGIN if reader else LOGIN
    require(parameters['user']==expected,'fence_audit_login_unregistered')
    raw=connect(**dict(parameters,dbname=database),connect_timeout=20,autocommit=True,
        row_factory=dict_row,application_name='owner-audit-sql-isolated-trial',
        options='-c search_path=pg_catalog -c statement_timeout=45000 -c lock_timeout=3000 -c idle_in_transaction_session_timeout=45000')
    try:
        require(raw.info.host==host.HOST and raw.info.dbname==database and raw.info.user==expected
            and raw.pgconn.ssl_in_use is True and raw.info.get_parameters().get('sslmode')=='verify-full'
            and raw.info.get_parameters().get('sslrootcert')==credential_parser.CA_PATH,'fence_audit_transport_invalid')
    except Exception:
        raw.close()
        raise
    return raw


def predecessor_catalog(admin):
    return dict(databases=admin.execute("SELECT datname,pg_get_userbyid(datdba) AS owner,datallowconn,datconnlimit,datacl::text,shobj_description(oid,'pg_database') AS marker FROM pg_database WHERE datname<>%s ORDER BY datname",(DATABASE,)).fetchall(),
        roles=admin.execute('SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolconnlimit,rolbypassrls,rolconfig,shobj_description(oid,\'pg_authid\') AS marker FROM pg_roles WHERE NOT rolname=ANY(%s) ORDER BY rolname',(list(ROLES),)).fetchall(),
        memberships=admin.execute('SELECT a.rolname AS granted,m.rolname AS member,admin_option,inherit_option,set_option FROM pg_auth_members JOIN pg_roles a ON a.oid=roleid JOIN pg_roles m ON m.oid=member WHERE NOT a.rolname=ANY(%s) AND NOT m.rolname=ANY(%s) ORDER BY a.rolname,m.rolname',(list(ROLES),list(ROLES))).fetchall())


def install(admin, connect, parameters, ddl, passwords):
    require(admin.execute('SELECT datname FROM pg_database WHERE datname=%s',(DATABASE,)).fetchone() is None
        and admin.execute('SELECT rolname FROM pg_roles WHERE rolname=ANY(%s)',(list(ROLES),)).fetchall()==[],
        'fence_audit_names_consumed')
    before=hashlib.sha256(canonical(predecessor_catalog(admin))).hexdigest()
    with admin.transaction():
        for name in ROLES:
            admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {}').format(sql.Identifier(name),sql.Literal(4 if name in (LOGIN,READER_LOGIN) else -1)))
            admin.execute(sql.SQL('COMMENT ON ROLE {} IS {}').format(sql.Identifier(name),sql.Literal(MARKER)))
        admin.execute(sql.SQL('GRANT {} TO CURRENT_USER WITH ADMIN FALSE, INHERIT FALSE, SET TRUE').format(sql.Identifier(OWNER)))
        for cap,login in ((CAPABILITY,LOGIN),(READER_CAP,READER_LOGIN)):
            admin.execute(sql.SQL('GRANT {} TO {} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE').format(sql.Identifier(cap),sql.Identifier(login)))
    admin.execute(sql.SQL('CREATE DATABASE {} OWNER {} TEMPLATE template0').format(sql.Identifier(DATABASE),sql.Identifier(OWNER)))
    with admin.transaction():
        admin.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(OWNER)))
        admin.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(DATABASE),sql.Literal(MARKER)))
        admin.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(DATABASE)))
        for name in (host.ADMIN,LOGIN,READER_LOGIN):
            admin.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(DATABASE),sql.Identifier(name)))
    with connect_exact(connect,parameters,DATABASE) as c:
        with c.transaction():
            c.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')
            c.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(OWNER)))
            c.execute(sql.SQL('CREATE SCHEMA {} AUTHORIZATION {}').format(sql.Identifier(SCHEMA),sql.Identifier(OWNER)))
            c.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM PUBLIC').format(sql.Identifier(SCHEMA)))
            c.execute(sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(sql.Identifier(SCHEMA)))
            c.execute("CREATE FUNCTION app_tenant_id() RETURNS UUID LANGUAGE SQL STABLE AS $$ SELECT NULLIF(current_setting('app.tenant_id',true),'')::UUID $$")
            c.execute('REVOKE ALL ON FUNCTION app_tenant_id() FROM PUBLIC')
            c.execute(ddl)
            # Session identity, not caller-changeable GUCs alone, fixes the
            # allowed tenant/worker. The NOLOGIN schema owner can seed fixtures.
            condition=sql.SQL("current_user={} OR (tenant_id={}::uuid AND worker_id={}::uuid AND session_user::text IN ({},{}))").format(sql.Literal(OWNER),sql.Literal(TENANT),sql.Literal(WORKER),sql.Literal(LOGIN),sql.Literal(READER_LOGIN))
            c.execute(sql.SQL('CREATE POLICY audit_identity_scope ON {} AS RESTRICTIVE USING ({}) WITH CHECK ({})').format(sql.Identifier(TABLE),condition,condition))
            for cap in (CAPABILITY,READER_CAP):
                c.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(SCHEMA),sql.Identifier(cap)))
                c.execute(sql.SQL('GRANT EXECUTE ON FUNCTION app_tenant_id() TO {}').format(sql.Identifier(cap)))
                c.execute(sql.SQL('GRANT SELECT ON {} TO {}').format(sql.Identifier(TABLE),sql.Identifier(cap)))
            c.execute(sql.SQL('GRANT INSERT ON {} TO {}').format(sql.Identifier(TABLE),sql.Identifier(CAPABILITY)))
    with admin.transaction():
        for login,password in passwords.items():
            encrypted=admin.pgconn.encrypt_password(password.encode(),login.encode(),b'scram-sha-256')
            require(encrypted.startswith(b'SCRAM-SHA-256$'),'fence_audit_password_encoding')
            admin.execute(sql.SQL('ALTER ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(login),sql.Literal(encrypted.decode('ascii'))))
    require(hashlib.sha256(canonical(predecessor_catalog(admin))).hexdigest()==before,'fence_audit_predecessor_drift')
    return before


@contextmanager
def restricted(connect, parameters, *, reader):
    raw=connect_exact(connect,parameters,DATABASE,reader=reader)
    try:
        login,cap=(READER_LOGIN,READER_CAP) if reader else (LOGIN,CAPABILITY)
        identity=raw.execute('SELECT session_user::text AS login,current_user::text AS role').fetchone()
        tls=raw.execute('SELECT ssl,version FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()
        require(identity==dict(login=login,role=login) and tls is not None and tls['ssl'] is True
            and tls['version'] in ('TLSv1.2','TLSv1.3'),'fence_audit_tls_login_invalid')
        raw.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(cap)))
        raw.execute(sql.SQL('SET search_path TO {},pg_catalog').format(sql.Identifier(SCHEMA)))
        yield raw,tls
    finally:
        raw.close()


@contextmanager
def scoped(raw, *, tenant=TENANT, worker=WORKER, reader=False):
    with raw.transaction():
        if reader:
            raw.execute('SET TRANSACTION READ ONLY')
        raw.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
        raw.execute("SELECT set_config('app.execution_worker_id',%s,true)",(worker,))
        yield


def insert(raw, number):
    item,data=payload(number); w=item.work
    return raw.execute('INSERT INTO owner_codex_worker_audit_events VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant_id,owner_object_id,task_id,phase) DO NOTHING',
        (TENANT,w.owner.object_id,w.task_id,'intent',WORKER,w.attempt_id,_digest(item.payload()),hashlib.sha256(data).hexdigest(),data.decode())).rowcount


def denied(raw, statement, parameters=(), *, expected='42501', tenant=TENANT, worker=WORKER):
    try:
        with scoped(raw,tenant=tenant,worker=worker):
            raw.execute(statement,parameters)
            raise ValueError('fence_audit_denial_was_allowed')
    except ValueError:
        raise
    except Exception as error:
        require(getattr(error,'sqlstate',None)==expected,'fence_audit_denial_wrong_cause')
    require(raw.info.transaction_status==0,'fence_audit_denial_left_transaction')


def qualify(connect, parameters, passwords, admin, *, passed):
    writer=dict(parameters,user=LOGIN,password=passwords[LOGIN])
    reader=dict(parameters,user=READER_LOGIN,password=passwords[READER_LOGIN])
    tls_records=[]; denial_count=0
    for is_reader,params,login,cap in ((False,writer,LOGIN,CAPABILITY),(True,reader,READER_LOGIN,READER_CAP)):
        with restricted(connect,params,reader=is_reader) as (c,tls):
            require(c.execute('SELECT session_user::text AS login,current_user::text AS role').fetchone()==dict(login=login,role=cap),'fence_audit_role_invalid')
            attrs=c.execute('SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=ANY(%s) ORDER BY rolname',([login,cap],)).fetchall()
            require(len(attrs)==2 and all(r['rolcanlogin']==(r['rolname']==login) and all(r[k] is False for k in ('rolsuper','rolinherit','rolcreaterole','rolcreatedb','rolreplication','rolbypassrls')) for r in attrs),'fence_audit_role_privileged')
            membership=c.execute('SELECT a.rolname AS granted,admin_option,inherit_option,set_option FROM pg_auth_members JOIN pg_roles a ON a.oid=roleid JOIN pg_roles m ON m.oid=member WHERE m.rolname=%s',(login,)).fetchall()
            require(membership==[dict(granted=cap,admin_option=False,inherit_option=False,set_option=True)],'fence_audit_membership_invalid')
            table=c.execute('SELECT relrowsecurity,relforcerowsecurity,pg_get_userbyid(relowner) AS owner FROM pg_class JOIN pg_namespace n ON n.oid=relnamespace WHERE n.nspname=%s AND relname=%s',(SCHEMA,TABLE)).fetchone()
            require(table==dict(relrowsecurity=True,relforcerowsecurity=True,owner=OWNER),'fence_audit_rls_invalid')
            for privilege in ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'):
                value=c.execute('SELECT has_table_privilege(current_user,%s,%s) AS allowed',(SCHEMA+'.'+TABLE,privilege)).fetchone()['allowed']
                require(value==(privilege=='SELECT' or (not is_reader and privilege=='INSERT')),'fence_audit_table_acl_invalid')
            require(c.execute('SELECT has_schema_privilege(current_user,%s,\'CREATE\') AS allowed',(SCHEMA,)).fetchone()['allowed'] is False,'fence_audit_schema_acl_invalid')
            tls_records.append(dict(login=login,capability=cap,sslmode='verify-full',protocol=tls['version']))
    passed.append(CHECKS[0])
    with restricted(connect,reader,reader=True) as (c,_):
        with scoped(c,reader=True):
            require(c.execute('SELECT count(*) AS count FROM owner_codex_worker_audit_events').fetchone()['count']==0,'fence_audit_fresh_row_required')
    with restricted(connect,writer,reader=False) as (c,_):
        with scoped(c):
            require(insert(c,1)==1,'fence_audit_insert_failed')
    with restricted(connect,reader,reader=True) as (c,_):
        with scoped(c,reader=True):
            rows=c.execute('SELECT * FROM owner_codex_worker_audit_events').fetchall()
            item,data=payload(1)
            require(len(rows)==1 and rows[0]['envelope_json']==data.decode()
                and rows[0]['record_sha256']==hashlib.sha256(data).hexdigest(),'fence_audit_independent_row_mismatch')
    passed.append(CHECKS[1])
    for is_reader,params in ((False,writer),(True,reader)):
        with restricted(connect,params,reader=is_reader) as (c,_):
            statements=['UPDATE owner_codex_worker_audit_events SET record_sha256=record_sha256',
                'DELETE FROM owner_codex_worker_audit_events','TRUNCATE owner_codex_worker_audit_events',
                'CREATE TABLE forbidden_audit_object(x integer)',sql.SQL('SET ROLE {}').format(sql.Identifier(OWNER))]
            if is_reader:
                statements.append('INSERT INTO owner_codex_worker_audit_events SELECT * FROM owner_codex_worker_audit_events')
                statements.append(sql.SQL('SET ROLE {}').format(sql.Identifier(CAPABILITY)))
            else:
                statements.append(sql.SQL('SET ROLE {}').format(sql.Identifier(READER_CAP)))
            for statement in statements:
                denied(c,statement);denial_count+=1
    passed.append(CHECKS[2])
    with restricted(connect,reader,reader=True) as (c,_):
        for tenant,worker in ((uid(999),WORKER),(TENANT,uid(998))):
            with scoped(c,tenant=tenant,worker=worker,reader=True):
                require(c.execute('SELECT count(*) AS count FROM owner_codex_worker_audit_events').fetchone()['count']==0,'fence_audit_scope_leak')
    with restricted(connect,writer,reader=False) as (c,_):
        # Even with changed GUCs the authenticated writer cannot invent another
        # tenant/worker. The existing row is untouched by these denied INSERTs.
        for tenant,worker in ((uid(999),WORKER),(TENANT,uid(998))):
            statement='INSERT INTO owner_codex_worker_audit_events SELECT %s,owner_object_id,%s,phase,%s,%s,binding_sha256,record_sha256,envelope_json FROM owner_codex_worker_audit_events'
            denied(c,statement,(tenant,uid(50000),worker,uid(60000)));denial_count+=1
    passed.append(CHECKS[3])
    with restricted(connect,writer,reader=False) as (c,_):
        with scoped(c):
            require(insert(c,1)==0,'fence_audit_duplicate_rearmed')
    passed.append(CHECKS[4])
    # Deliberately discard the local commit acknowledgement. Reconnect, read
    # immutable evidence, and prove a second writer cannot create another row.
    with restricted(connect,writer,reader=False) as (c,_):
        with scoped(c):
            require(insert(c,2)==1,'fence_audit_lost_ack_first_failed')
    with restricted(connect,writer,reader=False) as (c,_):
        with scoped(c):
            require(insert(c,2)==0,'fence_audit_lost_ack_rearmed')
    passed.append(CHECKS[5])
    with admin.transaction():
        admin.execute(sql.SQL('ALTER ROLE {} NOLOGIN').format(sql.Identifier(READER_LOGIN)))
    try:
        with restricted(connect,reader,reader=True):
            raise ValueError('fence_audit_revoked_login_allowed')
    except ValueError:
        raise
    except Exception as error:
        require(getattr(error,'sqlstate',None)=='28000','fence_audit_revocation_wrong_cause')
    denial_count+=1
    passed.append(CHECKS[6])
    return dict(denied_privilege_probes=denial_count,positive_tls=tls_records,
        restricted_audit_sql_verified=True,synthetic_registration_only=True,
        protected_worker_receipts_verified=False,runtime_process_cessation_verified=False,
        website_owner_authenticated=False)


def main(admin_secret, ddl, unused_constraints, *, connect, emit=print):
    result=dict(status='unaccepted',operation_id=OPERATION,job_resource_id=JOB_ID,
        database=DATABASE,schema=SCHEMA,checks=[],source_rows_read=0,source_rows_written=0,
        production_changed=False,model_calls=0,agents_activated=0,automatic_retry=False,
        step_3_complete=False,step_4_started=False)
    admin=None;stage='input'
    try:
        require(hashlib.sha256(ddl.encode()).hexdigest()==DDL_SHA and unused_constraints==ddl,'fence_audit_schema_invalid')
        parameters=credential_parser.validate_admin_secret(admin_secret);credential_parser.require_ca_bundle()
        admin=connect_exact(connect,parameters,'postgres');stage='installation'
        passwords={LOGIN:secrets.token_urlsafe(48),READER_LOGIN:secrets.token_urlsafe(48)}
        before=install(admin,connect,parameters,ddl,passwords)
        stage='restricted_qualification'
        result.update(qualify(connect,parameters,passwords,admin,passed=result['checks']))
        require(hashlib.sha256(canonical(predecessor_catalog(admin))).hexdigest()==before,'fence_audit_predecessor_drift')
        result['checks'].append(CHECKS[7])
        result.update(status='synthetic_postgres_audit_sql_verified',predecessor_metadata_unchanged=True,
            predecessor_metadata_sha256=before)
        require(all(v.encode() not in canonical(result) for v in passwords.values()) and admin_secret.encode() not in canonical(result),'fence_audit_sensitive_output')
    except Exception as error:
        code=str(error) if isinstance(error,ValueError) else ''
        result.update(failure_stage=stage,code=code if re.fullmatch('fence_[a-z_]+',code) else 'fence_audit_trial_unaccepted',error_class=type(error).__name__)
        state=getattr(error,'sqlstate',None)
        result['sqlstate']=state if type(state)is str and re.fullmatch('[A-Z0-9]{5}',state) else None
    finally:
        if admin is not None:
            admin.close()
    require(len(canonical(result))<16384,'fence_audit_result_oversized')
    emit(PREFIX+canonical(result).decode('ascii'),flush=True)
    return result
