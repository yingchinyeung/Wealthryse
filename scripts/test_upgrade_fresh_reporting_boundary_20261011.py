import unittest
from unittest.mock import patch
import prepare_upgrade_fresh_reporting_remote_20261011 as c

class Boundaries(unittest.TestCase):
    def uri(self):return 'postgresql://fake:fictional@'+c.SOURCE_HOST+'/'+c.SOURCE_DB
    def test_redirects_discarded(self):
        with patch.dict(c.os.environ,{'DATABASE_ADMIN_URL':self.uri()+'?hostaddr=1.2.3.4&host=bad&service=bad'}),patch.object(c.psycopg,'connect') as connect:
            c.connect(c.TEST_DB)
            self.assertNotIn('?',connect.call_args.args[0]);self.assertEqual(connect.call_args.kwargs['host'],c.HOST)
            self.assertEqual(connect.call_args.kwargs['dbname'],c.TEST_DB);self.assertEqual(connect.call_args.kwargs['sslmode'],'verify-full')
    def test_source_writes_forbidden(self):
        with patch.dict(c.os.environ,{'DATABASE_ADMIN_URL':self.uri()}),patch.object(c.psycopg,'connect') as connect:
            with self.assertRaises(ValueError):c.connect(c.SOURCE_DB,readonly=False)
            connect.assert_not_called()
    def test_unknown_database_forbidden(self):
        with patch.dict(c.os.environ,{'DATABASE_ADMIN_URL':self.uri()}),self.assertRaises(ValueError):c.connect('production')
    def test_wrong_source_forbidden(self):
        with patch.dict(c.os.environ,{'DATABASE_ADMIN_URL':self.uri().replace(c.SOURCE_HOST,'other')}),self.assertRaises(ValueError):c.connect(c.TEST_DB)
    def test_exact_report_tables(self):
        self.assertEqual(len(c.REPORT_TABLES),5);self.assertFalse(any('nav' in t or 'cash' in t or 'position' in t for t in c.REPORT_TABLES))

if __name__=='__main__':unittest.main()
