"""Private candidate acceptance using the real website login, no source writes.

This tests application routing behind Easy Auth, not a real browser sign-in.
Identity headers are simulated only inside the non-ingress manual job.
"""
import asyncio
import json
import os
import psycopg
from psycopg.rows import dict_row
from urllib.parse import urlparse

TENANT = '00000000-0000-0000-0000-000000000001'
HOST = 'ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
TABLES = ('specialist_daily_reports', 'specialist_report_deliveries')
PREFIX = 'SPECIALIST_INBOX_ACCESS '


def validate_url(value):
    p = urlparse(value)
    if (p.scheme != 'postgresql' or p.hostname != HOST or p.path != '/ibkr_staging'
            or p.username != 'ibkr_stg_web' or not p.password or p.query != 'sslmode=require'):
        raise ValueError('Unregistered website database target')
    return value


def inspect(connection):
    role = connection.execute('SELECT current_user AS name, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
    if role['name'] != 'ibkr_stg_web' or any(role[k] for k in ('rolsuper','rolcreaterole','rolcreatedb','rolbypassrls')):
        raise ValueError('Elevated website database role')
    rows = []
    for table in TABLES:
        flags = connection.execute('''SELECT relrowsecurity, relforcerowsecurity,
            has_table_privilege(current_user,oid,'SELECT') AS readable,
            has_table_privilege(current_user,oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS mutable
            FROM pg_class WHERE oid=%s::regclass''', ('public.'+table,)).fetchone()
        if not flags or not flags['relrowsecurity'] or not flags['relforcerowsecurity'] or flags['mutable']:
            raise ValueError('Website report access is not forced-RLS and read-only')
        rows.append(dict(table=table, **flags))
    audit_read = connection.execute("SELECT has_table_privilege(current_user,'public.specialist_audit_events','SELECT') AS allowed").fetchone()['allowed']
    if audit_read: raise ValueError('Website must not read the raw specialist audit ledger')
    return dict(login='ibkr_stg_web', tables=rows, audit_ledger_read=False, source_rows_changed=0)


async def http_get(app, path, identity=None):
    messages=[]
    async def receive(): return {'type':'http.request','body':b'','more_body':False}
    async def send(message): messages.append(message)
    headers=[] if identity is None else [(b'x-ms-client-principal-id',identity['entra_object_id'].encode()),
                                         (b'x-ms-client-principal-name',identity['email'].encode())]
    await app({'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1',
        'method':'GET','scheme':'https','path':path,'raw_path':path.encode(),'query_string':b'',
        'headers':headers,'server':('private-acceptance',443),'client':('private-acceptance',1)},receive,send)
    start=next(m for m in messages if m['type']=='http.response.start')
    body=b''.join(m.get('body',b'') for m in messages if m['type']=='http.response.body')
    return start,body


def main():
    if os.environ.get('PORTFOLIO_TENANT_ID') != TENANT or os.environ.get('AUTHORIZATION_MODE') != 'database':
        raise ValueError('Exact tenant and database-only authorization required')
    mode=os.environ.get('INBOX_ACCEPTANCE_MODE')
    if mode not in ('inspect','test'): raise ValueError('Unregistered acceptance mode')
    with psycopg.connect(validate_url(os.environ['DATABASE_URL']),autocommit=True,connect_timeout=20,
            row_factory=dict_row,options='-c default_transaction_read_only=on -c statement_timeout=20000') as c:
        c.execute("SELECT set_config('app.tenant_id',%s,false)",(TENANT,))
        result=inspect(c)
        if mode=='test':
            if not all(r['readable'] for r in result['tables']): raise ValueError('Website reader grant missing')
            from ibkr_etl import web
            if web.TENANT_ID!=TENANT or web.AUTHORIZATION_MODE!='database': raise ValueError('Candidate authorization differs')
            owner=c.execute('''SELECT u.entra_object_id,u.email FROM app_users u JOIN app_user_roles r
                ON u.tenant_id=r.tenant_id AND u.entra_object_id=r.entra_object_id
                WHERE u.tenant_id=%s AND u.status='active' AND lower(u.email)='anthony.ying@wealthryse.ai'
                AND r.role_name='owner' AND r.role_status='active' AND r.effective_from<=now()
                AND (r.effective_to IS NULL OR r.effective_to>=now())''',(TENANT,)).fetchone()
            viewer=c.execute('''SELECT u.entra_object_id,u.email FROM app_users u WHERE u.tenant_id=%s
                AND u.status='active' AND EXISTS (SELECT 1 FROM app_user_roles r WHERE r.tenant_id=u.tenant_id
                AND r.entra_object_id=u.entra_object_id AND r.role_name='viewer' AND r.role_status='active'
                AND r.effective_from<=now() AND (r.effective_to IS NULL OR r.effective_to>=now()))
                AND NOT EXISTS (SELECT 1 FROM app_user_roles r WHERE r.tenant_id=u.tenant_id
                AND r.entra_object_id=u.entra_object_id AND r.role_name='owner' AND r.role_status='active') LIMIT 1''',(TENANT,)).fetchone()
            if not owner or not viewer: raise ValueError('Registered owner and viewer test identities required')
            paths=('/ai-operations','/api/ai-operations/daily-reports','/api/ai-operations/daily-reports/2026-10-02')
            checks=[]
            for label,identity in (('owner',owner),('viewer',viewer),('anonymous',None)):
                for path in paths:
                    response,body=asyncio.run(http_get(web.app,path,identity))
                    expected=200 if label=='owner' else 403
                    if label=='owner' and path.endswith('/2026-10-02'):
                        published=c.execute("SELECT count(*) AS n FROM specialist_report_deliveries WHERE tenant_id=%s AND report_date='2026-10-02' AND channel='website' AND state='delivered'",(TENANT,)).fetchone()['n']
                        expected=200 if published else 404
                    if response['status']!=expected: raise ValueError('Candidate authorization or inbox query failed')
                    cache=dict(response['headers']).get(b'cache-control',b'').decode()
                    if label=='owner' and 'no-store' not in cache: raise ValueError('Sensitive owner response must not be cached')
                    if label=='owner' and path==paths[1]:
                        data=json.loads(body)
                        if not isinstance(data.get('reports'),list): raise ValueError('Invalid inbox listing schema')
                    checks.append(dict(identity=label,path=path,status=response['status']))
            c.execute("SELECT set_config('app.tenant_id',%s,false)",('00000000-0000-0000-0000-000000000099',))
            for table in TABLES:
                if c.execute('SELECT count(*) AS n FROM '+table).fetchone()['n']!=0: raise ValueError('Tenant isolation failed')
            result.update(http_checks=checks,tenant_isolation_verified=True,real_browser_sign_in_verified=False,
                identity_headers_simulated_inside_private_job=True,source_mutations_attempted=0)
    print(PREFIX+json.dumps(dict(result,status='passed',mode=mode,emails_sent=0)),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print(PREFIX+json.dumps(dict(status='failed',error_type=type(error).__name__,
            reason=str(error) if isinstance(error,ValueError) else 'Private inbox acceptance failed')),flush=True)
        raise SystemExit(1)
