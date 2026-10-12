"""Inspect/grant/verify existing viewers' read access to exactly five active subjects."""
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

TENANT = '00000000-0000-0000-0000-000000000001'
SUBJECTS = ('U16088571', 'U16088606', 'alpha-world-ms', 'alpha-world-t', 'hong-kong-china')
NEW_SUBJECT = 'alpha-world-t'
EXPECTED_VIEWERS = 4
READ_LEVELS = frozenset(('read', 'report', 'analyze', 'approve', 'admin'))
WEB_SYMBOLS = ('def _authorized_subject_ids(', 'def _portfolio_access_context(', 'AUTHORIZATION_MODE', 'TENANT_ID')
QUERIES = {
    'users': 'SELECT * FROM public.app_users WHERE tenant_id=%s ORDER BY entra_object_id',
    'roles': 'SELECT * FROM public.app_user_roles WHERE tenant_id=%s ORDER BY entra_object_id,role_name,effective_from',
    'access': 'SELECT * FROM public.user_account_access WHERE tenant_id=%s ORDER BY entra_object_id,subject_id',
    'subjects': 'SELECT * FROM public.platform_subjects WHERE tenant_id=%s ORDER BY subject_id',
    'features': 'SELECT * FROM public.subject_feature_states WHERE tenant_id=%s ORDER BY subject_id,feature_key',
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def choose_viewers(state, now):
    eligible = set()
    for role in state['roles']:
        start = datetime.fromisoformat(str(role['effective_from']))
        end = datetime.fromisoformat(str(role['effective_to'])) if role['effective_to'] else None
        if role['role_name'] == 'viewer' and role['role_status'] == 'active' and start <= now and (end is None or end >= now):
            eligible.add(str(role['entra_object_id']).lower())
    return [u for u in state['users'] if u['status'] == 'active' and str(u['entra_object_id']).lower() in eligible]


def missing_access(state, viewers):
    active = {s['subject_id'] for s in state['subjects'] if s['lifecycle_status'] == 'active'}
    require(active == set(SUBJECTS), 'Active five-subject scope changed; inspect before expanding access')
    features = {(r['subject_id'], r['feature_key']): r['feature_state'] for r in state['features']}
    require(all(features.get((s, key)) == 'active' for s in SUBJECTS for key in ('viewer_access', 'website_analytics')),
            'An onboarded subject has inactive reader features; do not bypass onboarding')
    access = {(str(r['entra_object_id']).lower(), r['subject_id']): r['access_level'] for r in state['access']}
    require(bool(viewers), 'No active registered viewers found; do not create identities')
    missing = []
    for user in viewers:
        for subject in SUBJECTS:
            current = access.get((str(user['entra_object_id']).lower(), subject))
            require(current is None or current in READ_LEVELS,
                    'Existing non-reader grant requires reconciliation, not permission replacement')
            if current is None:
                missing.append((user['entra_object_id'], subject))
    return missing


def approved_scope(viewers, missing):
    """The owner's decision covers exactly the four existing viewers and the one new subject."""
    require(len(viewers) == EXPECTED_VIEWERS, 'Registered viewer count differs from the approved four')
    require(len(missing) == EXPECTED_VIEWERS and all(subject == NEW_SUBJECT for _, subject in missing)
            and len({str(identity).lower() for identity, _ in missing}) == EXPECTED_VIEWERS,
            'Missing grants are not exactly one alpha-world-t read grant per approved viewer')


def diagnostics(state, viewers):
    """Read-only facts for the probe report; never used to authorize a write."""
    lifecycle = {s['subject_id']: s['lifecycle_status'] for s in state['subjects']}
    features = {(r['subject_id'], r['feature_key']): r['feature_state'] for r in state['features']}
    return {'viewer_count': len(viewers),
            'active_subjects': sorted(s for s, status in lifecycle.items() if status == 'active'),
            'new_subject_lifecycle': lifecycle.get(NEW_SUBJECT),
            'new_subject_features': {key: features.get((NEW_SUBJECT, key)) for key in ('viewer_access', 'website_analytics')}}


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
             'server': ('private-reader-verification.invalid', 443)}
    await app(scope, receive, send)
    start = next(m for m in sent if m['type'] == 'http.response.start')
    return start['status'], b''.join(m.get('body', b'') for m in sent if m['type'] == 'http.response.body')


def runtime_checks(state, viewers):
    # These are isolated application tests, not gateway impersonation or browser sign-ins.
    os.environ['PGOPTIONS'] = '-c default_transaction_read_only=on -c statement_timeout=120000'
    from ibkr_etl import web
    require(web.TENANT_ID == TENANT and web.AUTHORIZATION_MODE == 'database', 'Database-only authorization required')
    checks = []
    for viewer in viewers:
        user = {'object_id': viewer['entra_object_id'], 'email': viewer['email'], 'role': 'viewer',
                'roles': ['viewer'], 'authorization_source': 'database'}
        visible = web._authorized_subject_ids(user, feature_key='viewer_access')
        require(set(visible) == set(SUBJECTS), 'Native viewer directory does not expose exactly five accounts')
        for subject in SUBJECTS:
            context = web._portfolio_access_context(subject, user)
            require(context['lifecycle_status'] == 'active' and not context['preview_mode'], 'Viewer received unpublished subject')
            status, body = asyncio.run(asgi_get(web.app, '/accounts/' + subject + '/overview', viewer))
            require(status == 200 and b'OWNER-ONLY ONBOARDING PREVIEW' not in body, 'Native viewer overview denied')
            checks.append({'identity_sha256': hashlib.sha256(viewer['entra_object_id'].encode()).hexdigest(),
                           'subject_id': subject, 'http_status': status})
        for path in ('/administration', '/account-onboarding'):
            status, _ = asyncio.run(asgi_get(web.app, path, viewer))
            require(status == 403, 'Viewer obtained administrative or onboarding access')
        status, body = asyncio.run(asgi_get(web.app, '/', viewer))
        require(status == 200 and all(('/accounts/' + subject + '/overview').encode() in body for subject in SUBJECTS),
                'Native reader home missing an onboarded account')
    status, _ = asyncio.run(asgi_get(web.app, '/accounts/' + NEW_SUBJECT + '/overview'))
    require(status == 403, 'Anonymous reader was permitted')
    return checks


def main():
    require(CONFIG['phase'] in ('probe', 'apply', 'verify'), 'Unsupported phase')
    value = urlparse(os.environ['DATABASE_URL'])
    require(value.scheme == 'postgresql' and value.hostname == 'ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
            and value.path == '/ibkr_staging' and 'sslmode=require' in value.query, 'Unapproved database target')
    phase = CONFIG['phase']
    web_source = Path('/app/src/ibkr_etl/web.py').read_bytes()
    web_sha256 = hashlib.sha256(web_source).hexdigest()
    # The probe records the installed website source; later phases are bound to that exact source.
    require((phase == 'probe' and CONFIG['web_sha256'] is None) or web_sha256 == CONFIG['web_sha256'],
            'Installed website source differs')
    web_symbols = {name: name.encode() in web_source for name in WEB_SYMBOLS}
    service = BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'], credential=DefaultAzureCredential(
        exclude_interactive_browser_credential=True))
    prior = None
    if phase != 'probe':
        blob = service.get_blob_client('internal-reports', CONFIG['probe_blob'])
        content = blob.download_blob().readall()
        require(hashlib.sha256(content).hexdigest() == CONFIG['probe_sha256'], 'Bound private probe differs')
        prior = json.loads(content)
        require(prior['status'] == 'probe_passed' and prior['web_sha256'] == web_sha256, 'Bound probe did not pass on this website source')
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row, connect_timeout=25,
                        options='-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=10000') as c:
        c.execute("SELECT set_config('app.tenant_id',%s,false)", (TENANT,))
        role = c.execute('SELECT current_user AS name,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        require(not role['rolsuper'] and not role['rolbypassrls'], 'Restricted service login required')
        def capture():
            return {name: [dict(row) for row in c.execute(query, (TENANT,))] for name, query in QUERIES.items()}
        state = capture()
        viewers = choose_viewers(state, datetime.now(timezone.utc))
        can_insert = c.execute("SELECT has_table_privilege(current_user,'public.user_account_access','INSERT') AS allowed").fetchone()['allowed']
        blockers = []
        try:
            missing = missing_access(state, viewers)
            if phase != 'verify':
                approved_scope(viewers, missing)
        except ValueError as error:
            # A probe reports why it must stop; apply and verify never continue past a failed check.
            if phase != 'probe':
                raise
            missing = []
            blockers.append(str(error))
        if phase == 'probe':
            if not can_insert:
                blockers.append('Restricted job cannot grant read access; do not escalate its role')
            if not all(web_symbols.values()):
                blockers.append('Installed website lacks the native authorization entry points used for verification')
        c.commit()
        inserted = 0
        if phase == 'apply':
            require(can_insert, 'Restricted job cannot grant read access; do not escalate its role')
            c.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ WRITE')
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (TENANT + ':five-account-viewer-access',))
            require(canonical(capture()) == canonical(prior['state']), 'Authorization registry drift; refuse writes')
            require(missing == [tuple(x) for x in prior['missing']], 'Bound missing-access list differs')
            for object_id, subject in missing:
                c.execute('INSERT INTO public.user_account_access(tenant_id,entra_object_id,subject_id,access_level,granted_at,granted_by) '
                          "VALUES(%s,%s,%s,'read',%s,%s)", (TENANT, object_id, subject, datetime.now(timezone.utc).isoformat(),
                           hashlib.sha256(b'claude-owner-authorized-five-account-viewer-read-access').hexdigest()))
                inserted += 1
            after = capture()
            require(all(canonical(after[k]) == canonical(state[k]) for k in state if k != 'access'), 'Write outside viewer grants')
            require(not missing_access(after, viewers), 'Read grant inventory incomplete')
            require(inserted == EXPECTED_VIEWERS and len(after['access']) == len(state['access']) + inserted and
                    all(row in after['access'] for row in state['access']), 'Existing entitlements were changed')
            added = [row for row in after['access'] if row not in state['access']]
            require(len(added) == EXPECTED_VIEWERS and all(row['subject_id'] == NEW_SUBJECT and row['access_level'] == 'read'
                                                           and str(row['tenant_id']) == TENANT for row in added),
                    'Scope exceeded four alpha-world-t read grants')
            c.execute("INSERT INTO public.audit_events(tenant_id,occurred_at,actor_type,actor_id,event_type,subject_id,outcome,details_json) "
                      "VALUES(%s,now(),'agent','claude-owner-authorized-viewer-access','viewer_read_access_granted',NULL,'succeeded',%s)",
                      (TENANT, canonical({'scope': list(SUBJECTS), 'new_subject': NEW_SUBJECT, 'grants': inserted,
                                         'viewer_count': len(viewers), 'probe_sha256': CONFIG['probe_sha256'],
                                         'higher_permissions_added': False})))
            c.commit()
            state = after
            missing = []
        if phase == 'verify':
            require(not missing and len(viewers) == EXPECTED_VIEWERS, 'A viewer still lacks access')
            require(all(canonical(state[k]) == canonical(prior['state'][k]) for k in state if k != 'access'),
                    'Non-entitlement registry changed after probe')
            checks = runtime_checks(state, viewers)
            require(len(checks) == EXPECTED_VIEWERS * len(SUBJECTS), 'Viewer/account verification inventory incomplete')
        else:
            checks = []
        c.rollback()
    status = phase + ('_blocked' if blockers else '_passed')
    result = {'status': status, 'phase': phase, 'state': state, 'missing': missing, 'blockers': blockers,
              'diagnostics': diagnostics(state, viewers), 'web_sha256': web_sha256, 'web_symbols': web_symbols,
              'viewer_count': len(viewers), 'subject_ids': list(SUBJECTS), 'can_insert': can_insert,
              'inserted_read_grants': inserted, 'configuration': CONFIG, 'runtime_checks': checks,
              'database_writes': phase == 'apply' and inserted > 0, 'existing_grants_preserved': True,
              'roles_identities_sources_reports_unchanged': True, 'real_viewer_browser_sign_in_tested': False,
              'identity_claims_simulated_only_inside_non_ingress_job': phase == 'verify'}
    content = canonical(result).encode()
    blob = service.get_blob_client('internal-reports', CONFIG['output_blob'])
    digest = hashlib.sha256(content).hexdigest()
    blob.upload_blob(content, overwrite=False, metadata={'sha256': digest}, content_settings=ContentSettings(content_type='application/json'))
    require(blob.download_blob().readall() == content, 'Protected private result readback differs')
    print(json.dumps({'status': status, 'viewers': len(viewers), 'missing_grants': len(missing), 'blockers': blockers,
                      'inserted_read_grants': inserted, 'can_insert': can_insert, 'runtime_checks': len(checks)}), flush=True)


if __name__ == '__main__':
    main()
