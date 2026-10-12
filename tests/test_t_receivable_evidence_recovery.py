import copy,json,sqlite3,unittest
from pathlib import Path
from ibkr_etl.parser import FlexRow
from ibkr_etl.position_recovery import normalized_position
from ibkr_etl.util import canonical_json,sha256_text
from restore_t_receivable_evidence_20261009 import SOURCES,parse_sources,write_rows,TENANT

class EvidenceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row
        self.addCleanup(self.c.close)
        self.c.executescript('''CREATE TABLE accounts(account_id TEXT,tenant_id TEXT);
          CREATE TABLE position_snapshots(account_id TEXT,snapshot_date TEXT,conid TEXT,model_code TEXT,
          currency TEXT,quantity_text TEXT,cost_basis_text TEXT,mark_price_text TEXT,market_value_text TEXT,
          unrealized_pnl_text TEXT,row_sha256 TEXT);
          CREATE TABLE flex_rows(account_id TEXT,section TEXT,record_type TEXT,source_key TEXT,report_date TEXT,
          conid TEXT,currency TEXT,row_sha256 TEXT,data_json TEXT,first_seen_run_id INTEGER,last_seen_run_id INTEGER,tenant_id TEXT);''')
        a={'accountId':'U16088606','reportDate':'20250829','conid':'721987245','currency':'TWD',
           'symbol':'2885.REC','listingExchange':'CORPACT','position':'10','positionValue':'100','markPrice':'10'}
        self.r=FlexRow('U16088606','OpenPositions','OpenPosition',a,'derived:test',sha256_text(canonical_json(a)),
                       '2025-08-29','721987245','TWD')
        p=normalized_position(self.r)
        self.c.execute('INSERT INTO accounts VALUES(?,?)',(self.r.account_id,TENANT))
        self.c.execute('INSERT INTO position_snapshots VALUES('+','.join('?' for _ in p)+')',tuple(p.values()))
        self.c.commit()

    def test_missing_only_idempotent_and_rollback(self):
        before=[tuple(r) for r in self.c.execute('SELECT * FROM position_snapshots')]
        self.assertEqual(write_rows(self.c,[self.r],{'2025-08-29':1}),1)
        self.assertEqual(write_rows(self.c,[self.r],{'2025-08-29':1}),0)
        self.assertEqual([tuple(r) for r in self.c.execute('SELECT * FROM position_snapshots')],before)
        self.c.rollback()
        self.assertEqual(self.c.execute('SELECT count(*) FROM flex_rows').fetchone()[0],0)

    def test_conflicting_or_cross_tenant_evidence_is_preserved(self):
        write_rows(self.c,[self.r],{'2025-08-29':1})
        for field,value in [('tenant_id','other'),('row_sha256','wrong'),('data_json','{}')]:
            self.c.execute('SAVEPOINT test')
            self.c.execute('UPDATE flex_rows SET '+field+'=?',(value,))
            with self.assertRaises(ValueError):write_rows(self.c,[self.r],{'2025-08-29':1})
            self.assertEqual(self.c.execute('SELECT '+field+' FROM flex_rows').fetchone()[0],value)
            self.c.execute('ROLLBACK TO test')

    def test_financial_or_tenant_drift_blocks_insert(self):
        for sql in ("UPDATE position_snapshots SET market_value_text='101'", "UPDATE accounts SET tenant_id='other'",
                    "DELETE FROM position_snapshots", "INSERT INTO position_snapshots SELECT * FROM position_snapshots"):
            self.c.execute('SAVEPOINT test');self.c.execute(sql)
            with self.assertRaises(ValueError):write_rows(self.c,[self.r],{'2025-08-29':1})
            self.assertEqual(self.c.execute('SELECT count(*) FROM flex_rows').fetchone()[0],0)
            self.c.execute('ROLLBACK TO test')

    def test_actual_archives_hashes_and_three_records(self):
        folder=Path(__file__).resolve().parents[1]/'.work/alpha-t-onboarding-completion-20261009/originals'
        if not folder.exists():self.skipTest('Private archive qualification fixture unavailable')
        originals={digest:(folder/(digest+'.xml')).read_bytes() for digest,_ in SOURCES.values()}
        rows=parse_sources(originals)
        self.assertEqual(len(rows),3)
        bad=dict(originals);bad[next(iter(bad))]=b'changed'
        with self.assertRaisesRegex(ValueError,'checksum'):parse_sources(bad)

if __name__=='__main__':unittest.main()
