import copy,json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
import backfill_alpha_t_cash_20261009 as m

ROOT=Path(__file__).resolve().parents[1]
class CashBackfillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw={d:(ROOT/'.work/alpha-ms-recovery-20261007'/(h+'.xml')).read_bytes() for d,h in m.SOURCES.items()}
        cls.rows=[r for d,h in m.SOURCES.items() for r in m.parse_source(cls.raw[d],d,h)]
        cls.data=json.loads((ROOT/'.work/alpha-world-t-accuracy-audit-20261009/live-inputs.json').read_bytes())
    def setUp(self):
        self.c=sqlite3.connect(':memory:');self.addCleanup(self.c.close)
        self.c.executescript('''CREATE TABLE cash_snapshots(account_id,snapshot_date,currency,ending_cash_text,settled_cash_text,fx_rate_to_base_text,row_sha256,last_seen_run_id,PRIMARY KEY(account_id,snapshot_date,currency));
CREATE TABLE flex_rows(account_id,section,record_type,source_key,report_date,conid,currency,row_sha256,data_json,first_seen_run_id,last_seen_run_id,tenant_id,UNIQUE(account_id,section,record_type,source_key));
CREATE TABLE nav_snapshots(account_id,snapshot_date,currency,cash_text);
CREATE TABLE fx_rates(account_id,rate_date,from_currency,to_currency,rate_text);''')
        self.c.executemany('INSERT INTO nav_snapshots VALUES(?,?,?,?)',[(r['account_id'],r['snapshot_date'],r['currency'],r['cash_text']) for r in self.data['nav_details'] if r['snapshot_date'] in m.DAYS])
        self.c.executemany('INSERT INTO fx_rates VALUES(?,?,?,?,?)',[(r['account_id'],r['rate_date'],r['from_currency'],r['to_currency'],r['rate_text']) for r in self.data['broker_fx'] if r['rate_date'] in m.DAYS])
        self.c.commit();self.runs=dict(zip(m.DAYS,(1001,1002)))
    def test_original_rows_and_financial_reconciliation(self):
        self.assertEqual(len(self.rows),10);m.validate_financials(self.c,self.rows)
    def test_insert_and_idempotence(self):
        self.assertEqual(m.write_rows(self.c,self.rows,self.runs),10)
        self.assertEqual(m.write_rows(self.c,self.rows,self.runs),0)
        self.assertEqual(self.c.execute('SELECT count(*) FROM flex_rows').fetchone()[0],10)
    def test_rollback_removes_all_writes(self):
        m.write_rows(self.c,self.rows,self.runs);self.c.rollback()
        self.assertEqual(m.existing(self.c,self.rows),0)
        self.assertEqual(self.c.execute('SELECT count(*) FROM flex_rows').fetchone()[0],0)
    def test_partial_failure_atomic(self):
        self.c.execute("CREATE TRIGGER injected_failure BEFORE INSERT ON cash_snapshots WHEN NEW.currency='TWD' BEGIN SELECT RAISE(ABORT,'injected'); END")
        self.c.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.c:m.write_rows(self.c,self.rows,self.runs)
        self.assertEqual(m.existing(self.c,self.rows),0)
        self.assertEqual(self.c.execute('SELECT count(*) FROM flex_rows').fetchone()[0],0)
    def test_conflicting_existing_cash_rejected(self):
        m.write_rows(self.c,self.rows,self.runs)
        self.c.execute("UPDATE cash_snapshots SET ending_cash_text='1' WHERE account_id='U16088571'")
        with self.assertRaisesRegex(ValueError,'conflicts'):m.write_rows(self.c,self.rows,self.runs)
    def test_partial_existing_rejected(self):
        m.write_rows(self.c,self.rows,self.runs)
        self.c.execute("DELETE FROM cash_snapshots WHERE currency='TWD'")
        with self.assertRaisesRegex(ValueError,'Partial'):m.existing(self.c,self.rows)
    def test_source_tampering_rejected(self):
        d=m.DAYS[0]
        with self.assertRaisesRegex(ValueError,'hash'):m.parse_source(self.raw[d]+b' ',d,m.SOURCES[d])
    def test_wrong_date_rejected(self):
        with self.assertRaises(ValueError):m.parse_source(self.raw[m.DAYS[0]],'2026-08-26',m.SOURCES[m.DAYS[0]])
    def test_cash_nav_mismatch_rejected(self):
        rows=copy.deepcopy(self.rows);rows[0]['ending_cash_text']='1'
        with self.assertRaisesRegex(ValueError,'stored NAV'):m.validate_financials(self.c,rows)
    def test_currency_sum_mismatch_rejected(self):
        rows=copy.deepcopy(self.rows);next(r for r in rows if r['currency']=='HKD')['ending_cash_text']='1'
        with self.assertRaisesRegex(ValueError,'Currency cash'):m.validate_financials(self.c,rows)
    def test_original_attributes_preserved_and_dated_keys(self):
        m.write_rows(self.c,self.rows,self.runs)
        for row in self.rows:
            stored=self.c.execute('SELECT data_json,row_sha256 FROM flex_rows WHERE account_id=? AND source_key=?',(row['account_id'],row['source_key'])).fetchone()
            self.assertEqual(json.loads(stored[0]),row['attributes']);self.assertEqual(m.sha(stored[0].encode()),stored[1])
    def test_nonfinite_rejected(self):
        with self.assertRaises(ValueError):m.number('NaN')

if __name__=='__main__':unittest.main()
