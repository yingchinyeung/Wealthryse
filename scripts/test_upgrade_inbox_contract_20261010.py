"""Both preserved inbox payloads, fake identities/DB only; no real viewer sign-in."""
import asyncio,contextlib,io,json,os,types,unittest
from unittest.mock import MagicMock,patch

PAYLOADS = {}  # Injected verbatim approved live source by the isolated preparer.


class InboxContracts(unittest.TestCase):
    def variants(self):
        self.assertEqual(len(PAYLOADS),2)
        for name,source in PAYLOADS.items():
            ns={'__name__':'qualification_import'};exec(compile(source,'<preserved-'+name+'>','exec'),ns)
            yield ns,('ibkr_stg_web' if name.endswith('-r2') else 'ibkr_web')
    def env(self,ns,login,mode='inspect'):return {'PORTFOLIO_TENANT_ID':ns['TENANT'],'AUTHORIZATION_MODE':'database','INBOX_ACCEPTANCE_MODE':mode,'DATABASE_URL':'postgresql://'+login+':synthetic-password@'+ns['HOST']+'/ibkr_staging?sslmode=require'}
    def connection(self,login,**changes):
        conn=MagicMock();role=dict(name=login,rolsuper=False,rolcreaterole=False,rolcreatedb=False,rolbypassrls=False);flags=dict(relrowsecurity=True,relforcerowsecurity=True,readable=True,mutable=False)
        role.update({k:v for k,v in changes.items() if k in role});flags.update({k:v for k,v in changes.items() if k in flags})
        def query(sql,*args):
            value=role if 'FROM pg_roles' in sql else flags if 'FROM pg_class' in sql else {'allowed':changes.get('audit',False)}
            return MagicMock(fetchone=MagicMock(return_value=value))
        conn.execute.side_effect=query;return conn
    def test_payload_count(self):self.assertEqual(len(list(self.variants())),2)
    def test_registered_url(self):
        for ns,login in self.variants():self.assertEqual(ns['validate_url'](self.env(ns,login)['DATABASE_URL']),self.env(ns,login)['DATABASE_URL'])
    def bad_url(self,old,new):
        for ns,login in self.variants():
            with self.subTest(login=login),self.assertRaises(ValueError):ns['validate_url'](self.env(ns,login)['DATABASE_URL'].replace(old,new))
    def test_wrong_host(self):self.bad_url('ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com','fake.invalid')
    def test_wrong_database(self):self.bad_url('/ibkr_staging','/other')
    def test_wrong_scheme(self):self.bad_url('postgresql://','postgres://')
    def test_missing_password(self):self.bad_url(':synthetic-password','')
    def test_missing_tls(self):self.bad_url('?sslmode=require','')
    def test_inspect_permissions(self):
        for ns,login in self.variants():
            result=ns['inspect'](self.connection(login));self.assertEqual(result['login'],login);self.assertEqual(len(result['tables']),2);self.assertFalse(result['audit_ledger_read'])
    def denied(self,**changes):
        for ns,login in self.variants():
            with self.subTest(login=login),self.assertRaises(ValueError):ns['inspect'](self.connection(login,**changes))
    def test_wrong_login(self):self.denied(name='administrator')
    def test_elevated_role(self):
        for name in ('rolsuper','rolcreaterole','rolcreatedb','rolbypassrls'):self.denied(**{name:True})
    def test_rls_required(self):self.denied(relrowsecurity=False)
    def test_force_rls_required(self):self.denied(relforcerowsecurity=False)
    def test_writes_forbidden(self):self.denied(mutable=True)
    def test_raw_audit_forbidden(self):self.denied(audit=True)
    def bad_env(self,key,value):
        for ns,login in self.variants():
            env=self.env(ns,login);env[key]=value
            with patch.dict(os.environ,env,clear=True),patch('psycopg.connect') as connect,self.assertRaises(ValueError):ns['main']()
            connect.assert_not_called()
    def test_tenant_required(self):self.bad_env('PORTFOLIO_TENANT_ID','other')
    def test_database_authorization_required(self):self.bad_env('AUTHORIZATION_MODE','disabled')
    def test_mode_required(self):self.bad_env('INBOX_ACCEPTANCE_MODE','unknown')
    def test_main_inspect(self):
        for ns,login in self.variants():
            conn=self.connection(login);connect=MagicMock();connect.return_value.__enter__.return_value=conn
            with patch.dict(os.environ,self.env(ns,login),clear=True),patch('psycopg.connect',connect),contextlib.redirect_stdout(io.StringIO()) as out:ns['main']()
            self.assertIn('"status": "passed"',out.getvalue());self.assertIn('default_transaction_read_only=on',connect.call_args.kwargs['options']);conn.commit.assert_not_called()
    def test_http_get_scope(self):
        for ns,login in self.variants():
            calls=[]
            async def app(scope,receive,send):
                calls.append(scope);self.assertEqual((await receive())['type'],'http.request');await send({'type':'http.response.start','status':403,'headers':[]});await send({'type':'http.response.body','body':b'restricted'})
            response,body=asyncio.run(ns['http_get'](app,'/private',{'entra_object_id':'fake-object','email':'fake@example.invalid'}))
            self.assertEqual(response['status'],403);self.assertEqual(body,b'restricted');self.assertEqual(calls[0]['method'],'GET');self.assertEqual(dict(calls[0]['headers'])[b'x-ms-client-principal-id'],b'fake-object')
    def test_missing_reader_grant_blocks_http(self):
        for ns,login in self.variants():
            conn=self.connection(login,readable=False);connect=MagicMock();connect.return_value.__enter__.return_value=conn
            with patch.dict(os.environ,self.env(ns,login,'test'),clear=True),patch('psycopg.connect',connect),self.assertRaises(ValueError):ns['main']()


if __name__=='__main__':unittest.main()
