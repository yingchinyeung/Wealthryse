import unittest
from unittest.mock import patch
import prepare_reporting_migration_clone_20261011 as c

class Boundaries(unittest.TestCase):
    def uri(self):return 'postgresql://fake:fictional@'+c.CREDENTIAL_HOST+'/'+c.CREDENTIAL_DB
    def test_redirect_discarded_tls_private(self):
        with patch.dict(c.os.environ,{'DATABASE_ADMIN_URL':self.uri()+'?hostaddr=1.2.3.4&service=bad'}),patch.object(c.psycopg,'connect') as call:
            c.connect(c.TEST_DB)
            self.assertNotIn('?',call.call_args.args[0]);self.assertEqual(call.call_args.kwargs['host'],c.HOST)
            self.assertEqual(call.call_args.kwargs['dbname'],c.TEST_DB);self.assertEqual(call.call_args.kwargs['sslmode'],'verify-full')
            self.assertIn('default_transaction_read_only=on',call.call_args.kwargs['options'])
    def test_source_writes_rejected(self):
        with patch.dict(c.os.environ,{'DATABASE_ADMIN_URL':self.uri()}),patch.object(c.psycopg,'connect') as call:
            with self.assertRaises(ValueError):c.connect(c.SOURCE_DB,readonly=False)
            call.assert_not_called()
    def test_production_database_rejected(self):
        with patch.dict(c.os.environ,{'DATABASE_ADMIN_URL':self.uri()}),self.assertRaises(ValueError):c.connect(c.CREDENTIAL_DB)
    def test_unknown_database_rejected(self):
        with patch.dict(c.os.environ,{'DATABASE_ADMIN_URL':self.uri()}),self.assertRaises(ValueError):c.connect('other')
    def test_wrong_credential_host_rejected(self):
        with patch.dict(c.os.environ,{'DATABASE_ADMIN_URL':self.uri().replace(c.CREDENTIAL_HOST,'other')}),self.assertRaises(ValueError):c.connect(c.TEST_DB)
    def test_wrong_credential_database_rejected(self):
        with patch.dict(c.os.environ,{'DATABASE_ADMIN_URL':self.uri().replace(c.CREDENTIAL_DB,c.SOURCE_DB)}),self.assertRaises(ValueError):c.connect(c.TEST_DB)
    def test_only_clone_write_boundary(self):
        with patch.dict(c.os.environ,{'DATABASE_ADMIN_URL':self.uri()}),patch.object(c.psycopg,'connect') as call:
            c.connect(c.TEST_DB,readonly=False)
            self.assertIn('default_transaction_read_only=off',call.call_args.kwargs['options'])
    def test_fragment_rejected(self):
        with patch.dict(c.os.environ,{'DATABASE_ADMIN_URL':self.uri()+'#bad'}),self.assertRaises(ValueError):c.connect(c.TEST_DB)

if __name__=='__main__':unittest.main()
