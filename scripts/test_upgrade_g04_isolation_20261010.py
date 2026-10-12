import unittest
from unittest.mock import patch
import create_upgrade_g04_database_remote_20261010 as c
import probe_upgrade_g04_preflight_20261010 as p
import probe_upgrade_g04_worker_20261010 as w
from urllib.parse import urlsplit, parse_qs
from read_upgrade_g04_worker_20261010 import exact_environment


class IsolationTests(unittest.TestCase):
    def uri(self):return 'postgresql://synthetic:fake@'+c.SOURCE_HOST+'/'+c.SOURCE_DB
    def test_fixed_target_overrides_connection_query(self):
        with patch.dict(c.os.environ,{'DATABASE_ADMIN_URL':self.uri()+'?host=unapproved&dbname=wrong'}),patch.object(c.psycopg,'connect') as connect:
            c.connect(c.TEST_DB)
            self.assertEqual(connect.call_args.kwargs['host'],c.HOST)
            self.assertEqual(connect.call_args.kwargs['dbname'],c.TEST_DB)
            self.assertEqual(connect.call_args.kwargs['port'],5432)
            self.assertEqual(connect.call_args.kwargs['sslmode'],'verify-full')
            self.assertIn('default_transaction_read_only=on',connect.call_args.kwargs['options'])
    def test_bad_targets_rejected(self):
        for database in ('production','',c.SOURCE_HOST):
            with self.subTest(database=database),patch.dict(c.os.environ,{'DATABASE_ADMIN_URL':self.uri()}),patch.object(c.psycopg,'connect') as connect:
                with self.assertRaises(ValueError):c.connect(database)
                connect.assert_not_called()
    def test_bad_references_rejected(self):
        for uri in ('https://fake@'+c.SOURCE_HOST+'/'+c.SOURCE_DB,
                    self.uri().replace(c.SOURCE_HOST,'unapproved'),self.uri().replace('/'+c.SOURCE_DB,'/other'),self.uri()+'#fragment'):
            with self.subTest(uri=uri),patch.dict(c.os.environ,{'DATABASE_ADMIN_URL':uri}),patch.object(c.psycopg,'connect') as connect:
                with self.assertRaises(ValueError):c.connect(c.TEST_DB)
                connect.assert_not_called()
    def test_preflight_wrong_source_rejected(self):
        with self.assertRaises(ValueError):p.redirect('postgresql://fake@unexpected/db')
    def test_worker_uri_removes_every_redirect_option(self):
        uri=w.isolated_uri(self.uri()+'?host=production&hostaddr=1.2.3.4&dbname=wrong&service=other&sslmode=disable')
        parsed=urlsplit(uri)
        self.assertEqual(parsed.hostname,w.HOST)
        self.assertEqual(parsed.path,'/'+w.DATABASE)
        self.assertEqual(parsed.port,5432)
        self.assertEqual(set(parse_qs(parsed.query)),{'sslmode','sslrootcert','connect_timeout'})
        self.assertEqual(parse_qs(parsed.query)['sslmode'],['verify-full'])
    def test_worker_uri_rejects_other_sources(self):
        for uri in ('https://fake@'+w.SOURCE+'/ibkr_staging',
                    self.uri().replace(w.SOURCE,'unapproved'),self.uri().replace('/ibkr_staging','/postgres'),self.uri()+'#fragment'):
            with self.subTest(uri=uri),self.assertRaises(ValueError):w.isolated_uri(uri)
    def test_execution_multiple_exact_variables(self):
        wanted=[{'name':'DATABASE_URL','secretRef':'database-url'}, {'name':'PORTFOLIO_TENANT_ID','value':'tenant'}]
        actual=[{'name':'PORTFOLIO_TENANT_ID','value':'tenant'}, {'name':'DATABASE_URL','secretRef':'cappjob-qa-job','value':''}]
        exact_environment('qa-job',actual,wanted)
    def test_execution_rejects_secret_values_and_wrong_aliases(self):
        wanted=[{'name':'DATABASE_URL','secretRef':'database-url'}]
        for bad in ({'name':'DATABASE_URL','secretRef':'unapproved'},
                    {'name':'DATABASE_URL','secretRef':'database-url','value':'not-allowed'}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):exact_environment('qa-job',[bad],wanted)
    def test_execution_rejects_missing_duplicate_and_extra_variables(self):
        wanted=[{'name':'MODE','value':'test'}]
        for bad in ([],wanted+wanted,wanted+[{'name':'EXTRA','value':'x'}]):
            with self.subTest(bad=bad),self.assertRaises(ValueError):exact_environment('qa-job',bad,wanted)
    def test_execution_rejects_changed_plain_value(self):
        with self.assertRaises(ValueError):exact_environment('qa-job',[{'name':'MODE','value':'production'}],[{'name':'MODE','value':'test'}])


if __name__=='__main__':unittest.main()
