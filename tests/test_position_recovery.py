import sqlite3
import unittest
from ibkr_etl.parser import FlexRow
from ibkr_etl.position_recovery import validate_scope, insert_missing_positions


class PositionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.row = FlexRow('U1','OpenPositions','OpenPosition',
            {'position':'2','markPrice':'10','positionValue':'20'},'source','hash','2026-08-27','123','USD')
        self.control=[dict(account_id='U1',date='2026-08-27',conid='123',currency='USD',quantity='2',price='10',value='20')]
        self.c=sqlite3.connect(':memory:')
        self.c.executescript('''CREATE TABLE instruments(conid TEXT PRIMARY KEY);
            INSERT INTO instruments VALUES('123');
            CREATE TABLE position_snapshots(account_id TEXT,snapshot_date TEXT,conid TEXT,model_code TEXT,
            currency TEXT,quantity_text TEXT,cost_basis_text TEXT,mark_price_text TEXT,market_value_text TEXT,
            unrealized_pnl_text TEXT,row_sha256 TEXT,last_seen_run_id INTEGER,
            PRIMARY KEY(account_id,snapshot_date,conid,model_code));
            CREATE TABLE flex_rows(account_id TEXT,section TEXT,record_type TEXT,source_key TEXT,report_date TEXT,
            conid TEXT,currency TEXT,row_sha256 TEXT,data_json TEXT,first_seen_run_id INTEGER,last_seen_run_id INTEGER,
            UNIQUE(account_id,section,record_type,source_key));''')

    def tearDown(self):self.c.close()

    def test_insert_and_idempotent_without_changing_run(self):
        self.assertEqual(insert_missing_positions(self.c,[self.row],self.control,1),1)
        self.assertEqual(insert_missing_positions(self.c,[self.row],self.control,2),0)
        self.assertEqual(self.c.execute('SELECT last_seen_run_id FROM position_snapshots').fetchone()[0],1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM flex_rows').fetchone()[0],1)

    def test_conflict_preserved(self):
        insert_missing_positions(self.c,[self.row],self.control,1)
        self.c.execute("UPDATE position_snapshots SET market_value_text='21'")
        with self.assertRaises(ValueError):insert_missing_positions(self.c,[self.row],self.control,2)
        self.assertEqual(self.c.execute('SELECT market_value_text FROM position_snapshots').fetchone()[0],'21')

    def test_cost_pnl_and_original_row_hash_conflicts_preserved(self):
        for column, value in [('cost_basis_text','19'),('unrealized_pnl_text','1'),('row_sha256','other')]:
            with self.subTest(column=column):
                self.c.execute('DELETE FROM position_snapshots')
                insert_missing_positions(self.c,[self.row],self.control,1)
                self.c.execute('UPDATE position_snapshots SET '+column+'=?',(value,))
                with self.assertRaises(ValueError):
                    insert_missing_positions(self.c,[self.row],self.control,2)
                self.assertEqual(self.c.execute('SELECT '+column+' FROM position_snapshots').fetchone()[0],value)

    def test_nonfinite_optional_source_values_rejected(self):
        self.row.attributes['costBasisMoney']='NaN'
        with self.assertRaises(ValueError):
            validate_scope([self.row],self.control)

    def test_wrong_date_and_missing_holdings_rejected(self):
        with self.assertRaises(ValueError):validate_scope([],self.control)
        control=[dict(self.control[0],date='2026-08-28')]
        with self.assertRaises(ValueError):validate_scope([self.row],control)

    def test_duplicate_and_bad_numeric_rejected(self):
        with self.assertRaises(ValueError):validate_scope([self.row,self.row],self.control)
        self.row.attributes['positionValue']='NaN'
        with self.assertRaises(ValueError):validate_scope([self.row],self.control)

    def test_conflicting_original_evidence_rejected(self):
        self.c.execute("INSERT INTO flex_rows(account_id,section,record_type,source_key,row_sha256) VALUES('U1','OpenPositions','OpenPosition','source','other')")
        with self.assertRaises(ValueError):insert_missing_positions(self.c,[self.row],self.control,1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM position_snapshots').fetchone()[0],0)

    def test_values_and_currency_reconciled(self):
        for field,value in [('value','21'),('currency','HKD'),('quantity','3'),('price','11')]:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):validate_scope([self.row],[dict(self.control[0],**{field:value})])


if __name__=='__main__':unittest.main()
