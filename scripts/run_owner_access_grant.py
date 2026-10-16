"""Probe, grant and verify the owner role for one existing registered user, in one job execution."""
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row

TENANT = '00000000-0000-0000-0000-000000000001'
TARGET_EMAIL = 'chloe.yoo@wealthryse.ai'
WEB_SYMBOLS = ('def _authorized_subject_ids(', 'def _owner_user(', 'AUTHORIZATION_MODE', 'TENANT_ID')
QUERIES = {
    'users': 'SELECT * FROM public.app_users WHERE tenant_id=%s ORDER BY entra_object_id',
    'roles': 'SELECT * FROM public.app_user_roles WHERE tenant_id=%s ORDER BY entra_object_id,role_name,effective_from',
    'access': 'SELECT * FROM public.user_account_access WHERE tenant_id=%s ORDER BY entra_object_id,subject_id',
    'subjects': 'SELECT * FROM public.platform_subjects WHERE tenant_id=%s ORDER BY subject_id',
}
PRIVILEGES = (('public.app_user_roles', 'INSERT'), ('public.app_users', 'UPDATE'), ('public.audit_events', 'INSERT'))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def current_roles(state, now):
    roles = {}
    for role in state['roles']:
        start = datetime.fromisoformat(str(role['effective_from']))
        end = datetime.fromisoformat(str(role['effective_to'])) if role['effective_to'] else None
        if role['role_status'] == 'active' and start <= now and (end is None or end >= now):
            roles.setdefault(str(role['entra_object_id']).lower(), set()).add(role['role_name'])
    return roles


def inspect(state, now):
    """Return (target user, current owners) or raise why the grant must not proceed."""
    targets = [u for u in state['users'] if str(u['email']).lower() == TARGET_EMAIL]
    require(len(targets) == 1, 'Target is not exactly one registered user; do not create identities')
    target = targets[0]
    require(target['status'] == 'active', 'Target user is not active')
    roles = current_roles(state, now)
    owners = sorted(oid for oid, names in roles.items() if 'owner' in names)
    require(len(owners) == 1, 'Expected exactly one current owner before the grant')
    target_id = str(target['entra_object_id']).lower()
    require(target_id not in owners, 'Target already holds the owner role')
    require(bool(roles.get(target_id)), 'Target has no current role; registration is incomplete')
    return target, owners


def diagnostics(state, now):
    """Read-only facts for the probe report; never used to authorize a write."""
    roles = current_roles(state, now)
    target = [u for u in state['users'] if str(u['email']).lower() == TARGET_EMAIL]
    target_id = str(target[0]['entra_object_id']).lower() if target else None
    return {'target_registered': bool(target), 'target_status': target[0]['status'] if target else None,
            'target_roles': sorted(roles.get(target_id, ())) if target_id else [],
            'owner_count': sum('owner' in names for names in roles.values()),
            'active_subjects': sorted(s['subject_id'] for s in state['subjects'] if s['lifecycle_status'] == 'active')}


async def asgi_get(app, path, identity=None):
    sent, complete = [], asyncio.Event()
    delivered = False
    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        await complete.wait()
        return {'type': 'http.disconnect'}
    async def send(message):
        sent.append(message)
        if message['type'] == 'http.response.body' and not message.get('more_body'):
            complete.set()
    headers = [] if identity is None else [
        (b'x-ms-client-principal-id', identity['entra_object_id'].encode()),
        (b'x-ms-client-principal-name', identity['email'].encode())]
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'}, 'http_version': '1.1',
             'method': 'GET', 'scheme': 'https', 'path': path, 'raw_path': path.encode(), 'root_path': '',
             'query_string': b'', 'headers': headers, 'client': ('127.0.0.1', 1),
             'server': ('private-owner-verification.invalid', 443)}
    await app(scope, receive, send)
    start = next(m for m in sent if m['type'] == 'http.response.start')
    return start['status'], b''.join(m.get('body', b'') for m in sent if m['type'] == 'http.response.body')


def runtime_checks(state, target, now):
    # Isolated application tests inside the non-ingress job, not gateway impersonation or browser sign-ins.
    os.environ['PGOPTIONS'] = '-c default_transaction_read_only=on -c statement_timeout=120000'
    from ibkr_etl import web
    require(web.TENANT_ID == TENANT and web.AUTHORIZATION_MODE == 'database', 'Database-only authorization required')
    active = sorted(s['subject_id'] for s in state['subjects'] if s['lifecycle_status'] == 'active')
    checks = []
    status, body = asyncio.run(asgi_get(web.app, '/', target))
    require(status == 200 and all(('/accounts/' + s + '/overview').encode() in body for s in active),
            'Owner home is missing an active account')
    for subject in active:
        status, _ = asyncio.run(asgi_get(web.app, '/accounts/' + subject + '/overview', target))
        require(status == 200, 'Owner overview denied for ' + subject)
        checks.append({'path': '/accounts/' + subject + '/overview', 'http_status': status})
    for path in ('/administration', '/account-onboarding'):
        status, _ = asyncio.run(asgi_get(web.app, path, target))
        require(status < 400, 'Owner page denied: ' + path)
        checks.append({'path': path, 'http_status': status})
    roles = current_roles(state, now)
    others = [u for u in state['users'] if u['status'] == 'active'
              and 'owner' not in roles.get(str(u['entra_object_id']).lower(), ())]
    for user in others:
        status, _ = asyncio.run(asgi_get(web.app, '/administration', user))
        require(status == 403, 'A non-owner obtained administration access')
    status, _ = asyncio.run(asgi_get(web.app, '/administration'))
    require(status == 403, 'Anonymous administration access was permitted')
    return checks


def emit(kind, value):
    # The controller reads these lines from the job's console logs; storage is private-network only.
    print('OWNER_GRANT ' + kind + ' ' + canonical(value), flush=True)


def main():
    require(CONFIG['mode'] in ('probe', 'grant'), 'Unsupported mode')
    value = urlparse(os.environ['DATABASE_URL'])
    require(value.scheme == 'postgresql' and value.hostname == 'ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
            and value.path == '/ibkr_staging' and 'sslmode=require' in value.query, 'Unapproved database target')
    web_source = Path('/app/src/ibkr_etl/web.py').read_bytes()
    web_symbols = {name: name.encode() in web_source for name in WEB_SYMBOLS}
    now = datetime.now(timezone.utc)
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row, connect_timeout=25,
                        options='-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=10000') as c:
        c.execute("SELECT set_config('app.tenant_id',%s,false)", (TENANT,))
        role = c.execute('SELECT current_user AS name,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        require(not role['rolsuper'] and not role['rolbypassrls'], 'Restricted service login required')
        def capture():
            return {name: [dict(row) for row in c.execute(query, (TENANT,))] for name, query in QUERIES.items()}
        state = capture()
        privileges = {table + ':' + kind: c.execute('SELECT has_table_privilege(current_user,%s,%s) AS allowed',
                                                    (table, kind)).fetchone()['allowed'] for table, kind in PRIVILEGES}
        blockers = []
        target = owners = None
        try:
            target, owners = inspect(state, now)
        except ValueError as error:
            blockers.append(str(error))
        if not all(privileges.values()):
            blockers.append('Restricted job cannot write the role registry; do not escalate its role')
        if not all(web_symbols.values()):
            blockers.append('Installed website lacks the authorization entry points used for verification')
        c.commit()
        emit('PROBE', {'status': 'probe_blocked' if blockers else 'probe_passed', 'blockers': blockers,
                       'privileges': privileges, 'diagnostics': diagnostics(state, now),
                       'web_sha256': hashlib.sha256(web_source).hexdigest()})
        if blockers or CONFIG['mode'] == 'probe':
            c.rollback()
            return
        c.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ WRITE')
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (TENANT + ':owner-access-grant',))
        require(canonical(capture()) == canonical(state), 'Authorization registry drift; refuse writes')
        target_id = target['entra_object_id']
        c.execute("INSERT INTO public.app_user_roles(tenant_id,entra_object_id,role_name,role_status,effective_from,"
                  "granted_by_identity_sha256,reason) VALUES(%s,%s,'owner','active',now(),%s,%s)",
                  (TENANT, target_id, hashlib.sha256(b'claude-owner-authorized-owner-access-grant').hexdigest(),
                   'Owner asked that this user have the same website access as the owner'))
        c.execute("UPDATE public.app_users SET role_name='owner',updated_at=%s WHERE tenant_id=%s AND entra_object_id=%s",
                  (now.isoformat(), TENANT, target_id))
        after = capture()
        require(canonical(after['access']) == canonical(state['access'])
                and canonical(after['subjects']) == canonical(state['subjects']), 'Write outside the role registry')
        require(all(row in after['roles'] for row in state['roles']) and len(after['roles']) == len(state['roles']) + 1,
                'Existing roles were changed')
        added = [row for row in after['roles'] if row not in state['roles']]
        require(len(added) == 1 and added[0]['role_name'] == 'owner' and added[0]['entra_object_id'] == target_id,
                'Scope exceeded one owner role for the target')
        changed = [row for row in after['users'] if row not in state['users']]
        require(len(after['users']) == len(state['users']) and len(changed) == 1
                and changed[0]['entra_object_id'] == target_id and changed[0]['role_name'] == 'owner',
                'Scope exceeded the target user row')
        c.execute("INSERT INTO public.audit_events(tenant_id,occurred_at,actor_type,actor_id,event_type,subject_id,outcome,details_json) "
                  "VALUES(%s,now(),'agent','claude-owner-authorized-owner-access','owner_role_granted',NULL,'succeeded',%s)",
                  (TENANT, canonical({'target_identity_sha256': hashlib.sha256(target_id.encode()).hexdigest(),
                                      'entitlements_changed': False, 'existing_roles_preserved': True})))
        c.commit()
        emit('APPLY', {'status': 'apply_passed', 'owner_role_granted': True})
        state = capture()
        c.rollback()
    later = datetime.now(timezone.utc)
    roles = current_roles(state, later)
    new_owners = sorted(oid for oid, names in roles.items() if 'owner' in names)
    require(new_owners == sorted(owners + [str(target['entra_object_id']).lower()]), 'Owner set is not the prior owner plus the target')
    checks = runtime_checks(state, target, later)
    emit('VERIFY', {'status': 'verify_passed', 'runtime_checks': checks, 'owner_count': len(new_owners),
                    'real_browser_sign_in_tested': False})


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        emit('FAIL', {'error_type': type(error).__name__, 'message': str(error)[:300]})
        raise
