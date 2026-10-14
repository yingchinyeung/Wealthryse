"""Released files survive input restatement, but approval is never inherited."""
from contextlib import nullcontext
import hashlib,sqlite3,unittest
from unittest.mock import patch
from fastapi import HTTPException
from ibkr_etl import web
from ibkr_etl.monthly_reports import build_monthly_report_catalog,MonthlyReportError

SUBJECT='alpha-world-t'
PERIOD='2026-09-30'
CONTENT=b'private-test-report-not-portfolio-data'
DIGEST=hashlib.sha256(CONTENT).hexdigest()
VIEWER={'object_id':'test-viewer','email':'viewer@example.test','role':'viewer'}

def snapshot(key='new',period=PERIOD):
    return {'snapshot_id':key,'subject_id':SUBJECT,'period_end':period,'data_as_of':period,'quality_status':'pass'}

def releases(key='old',period=PERIOD):
    return [{**snapshot(key,period),'artifact_release_id':n,'report_type':kind,
        'container_name':'internal-reports','blob_name':f'released/{SUBJECT}/{period}/{kind}.{ext}',
        'artifact_sha256':DIGEST} for n,(kind,ext) in enumerate((('factsheet','pdf'),('presentation','pptx')),1)]

def reviews():
    return [{**snapshot(),'generation_run_id':n,'report_type':kind,'status':'awaiting_approval',
        'output_reference':f'internal-reports/review/{SUBJECT}/{PERIOD}/{kind}.{ext}',
        'output_sha256':DIGEST} for n,(kind,ext) in enumerate((('factsheet','pdf'),('presentation','pptx')),10)]

def catalog(rows=None,inputs=None,runs=None):
    return build_monthly_report_catalog(inputs if inputs is not None else [snapshot()],
        runs if runs is not None else reviews(), rows if rows is not None else releases(),
        account_id=SUBJECT,container='internal-reports')

class CatalogHandoverTests(unittest.TestCase):
    def test_old_pair_remains_released_new_pair_separately_reviewable(self):
        month=catalog()[0]
        self.assertTrue(month['ready']);self.assertTrue(month['replacement_pending']);self.assertTrue(month['review_ready'])
        self.assertEqual(month['status'],'replacement_pending')
        self.assertEqual({r['snapshot_id'] for r in month['released_artifacts']},{'old'})
        self.assertTrue(all(r['review_only'] for r in month['review_artifacts']))
        self.assertTrue(all('/review-artifacts/' in r['download_url'] for r in month['artifacts']))
    def test_viewer_never_receives_private_replacement(self):
        month=catalog()[0]
        with patch.object(web,'_portfolio_access_context',return_value={'preview_mode':False,
                'feature_states':{'factsheet':'active','presentation':'active'},'display_name':'Test','reporting_currency':'HKD'}),\
             patch.object(web,'_database',return_value=nullcontext(None)),\
             patch.object(web,'_monthly_report_catalog',return_value=[month]):
            row=web._monthly_report_payload(SUBJECT,VIEWER)['months'][0]
        self.assertEqual(row['artifacts'],month['released_artifacts'])
        self.assertEqual(row['review_artifacts'],[]);self.assertFalse(row['review_ready'])
    def test_generation_pending_keeps_only_approved_downloads(self):
        row=catalog(runs=[])[0]
        self.assertTrue(row['downloadable']);self.assertFalse(row['review_ready'])
        self.assertEqual(row['artifacts'],row['released_artifacts'])
    def test_no_prior_approval_is_not_made_downloadable_for_viewers(self):
        row=catalog(rows=[])[0]
        self.assertFalse(row['ready']);self.assertEqual(row['released_artifacts'],[])
    def test_partial_handover_is_explicit(self):
        rows=releases();rows[0]['snapshot_id']='new'
        row=catalog(rows=rows)[0]
        self.assertTrue(row['replacement_pending']);self.assertTrue(row['review_ready'])
    def test_complete_handover_clears_pending_marker(self):
        row=catalog(rows=releases('new'))[0]
        self.assertFalse(row['replacement_pending']);self.assertFalse(row['review_ready'])
    def test_revoked_type_is_not_reconstructed(self):
        row=catalog(rows=releases()[1:])[0]
        self.assertFalse(row['ready']);self.assertEqual(len(row['released_artifacts']),1)
    def test_a_released_factsheet_is_ready_without_a_deck(self):
        # Decks are no longer built: the month is complete with its factsheet alone.
        row=catalog(rows=releases()[:1])[0]
        self.assertTrue(row['ready']);self.assertEqual(len(row['released_artifacts']),1)
    def test_other_month_cannot_supply_current_month(self):
        row=catalog(rows=releases(period='2026-08-31'))[0]
        self.assertEqual(row['released_artifacts'],[])
    def test_duplicate_current_releases_fail_closed(self):
        with self.assertRaisesRegex(MonthlyReportError,'current release'):catalog(rows=releases()*2)
    def test_duplicate_active_months_fail_closed(self):
        with self.assertRaisesRegex(MonthlyReportError,'active reporting snapshot'):catalog(inputs=[snapshot(),snapshot('other')])
    def test_cross_subject_release_rejected(self):
        rows=releases();rows[0]['subject_id']='other'
        with self.assertRaises(MonthlyReportError):catalog(rows=rows)
    def test_ui_discloses_pending_replacement(self):
        page=web.account_page('U16088571','reports',None,VIEWER)
        self.assertIn('Previously approved files available; replacement pending',page)
        self.assertIn('replacements require a separate owner decision',page)

class SQLConnection:
    def __init__(self,db):self.db=db
    def execute(self,sql,args=()):return self.db.execute(sql.replace('%s','?'),args)

class ReleasedDownloadQueryTests(unittest.TestCase):
    """Execute the actual handler SQL, rather than mocking filter results."""
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.row_factory=lambda c,r:dict(zip([d[0] for d in c.description],r))
        self.db.executescript('''
            CREATE TABLE reporting_snapshots(snapshot_id TEXT,tenant_id TEXT,subject_id TEXT,period_end TEXT,status TEXT,quality_status TEXT);
            CREATE TABLE report_generation_runs(generation_run_id INTEGER,tenant_id TEXT,snapshot_id TEXT,report_type TEXT,status TEXT);
            CREATE TABLE report_release_decisions(release_decision_id INTEGER,tenant_id TEXT,snapshot_id TEXT,release_scope TEXT,status TEXT);
            CREATE TABLE report_artifact_releases(artifact_release_id INTEGER,tenant_id TEXT,release_decision_id INTEGER,generation_run_id INTEGER,
              container_name TEXT,blob_name TEXT,artifact_sha256 TEXT,release_scope TEXT,revoked_at TEXT,superseded_by_artifact_release_id INTEGER);
        ''')
        t=web.TENANT_ID
        self.db.execute('INSERT INTO reporting_snapshots VALUES (?,?,?,?,?,?)',('old',t,SUBJECT,PERIOD,'superseded','pass'))
        self.db.execute('INSERT INTO report_generation_runs VALUES (?,?,?,?,?)',(11,t,'old','factsheet','released_internal'))
        self.db.execute('INSERT INTO report_release_decisions VALUES (?,?,?,?,?)',(21,t,'old','internal_shareholder','approved'))
        self.db.execute('INSERT INTO report_artifact_releases VALUES (?,?,?,?,?,?,?,?,?,?)',
            (31,t,21,11,'internal-reports',f'released/{SUBJECT}/{PERIOD}/factsheet.pdf',DIGEST,'internal_shareholder',None,None))
    def download(self):
        with patch.object(web,'_database',return_value=nullcontext(SQLConnection(self.db))),\
             patch.object(web,'_portfolio_access_context',return_value={}),\
             patch.object(web,'download_blob_bytes',return_value=CONTENT):
            return web._download_monthly_report(subject_id=SUBJECT,artifact_release_id=31,user=VIEWER)
    def test_superseded_input_does_not_revoke_approved_artifact(self):
        response=self.download();self.assertEqual(response.body,CONTENT)
        self.assertEqual(response.headers['x-content-sha256'],DIGEST)
    def test_invalid_authority_and_artifact_states_are_denied(self):
        mutations=[('report_artifact_releases','revoked_at',"'revoked'"),
            ('report_artifact_releases','superseded_by_artifact_release_id','99'),
            ('report_artifact_releases','release_scope',"'public'"),
            ('report_release_decisions','status',"'pending'"),
            ('report_release_decisions','snapshot_id',"'new'"),
            ('report_release_decisions','release_scope',"'public'"),
            ('report_generation_runs','status',"'awaiting_approval'"),
            ('reporting_snapshots','status',"'draft'"),
            ('reporting_snapshots','quality_status',"'fail'"),
            ('reporting_snapshots','subject_id',"'other'"),
            ('report_artifact_releases','tenant_id',"'other'")]
        for table,column,value in mutations:
            with self.subTest(table=table,column=column):
                self.db.execute('SAVEPOINT trial')
                self.db.execute(f'UPDATE {table} SET {column}={value}')
                with self.assertRaises(HTTPException) as result:self.download()
                self.assertEqual(result.exception.status_code,404)
                self.db.execute('ROLLBACK TO trial');self.db.execute('RELEASE trial')
    def test_hash_corruption_still_denied(self):
        self.db.execute('UPDATE report_artifact_releases SET artifact_sha256=?',('a'*64,))
        with self.assertRaises(HTTPException) as result:self.download()
        self.assertEqual(result.exception.status_code,503)

class SupersessionTransactionTests(ReleasedDownloadQueryTests):
    def setUp(self):
        super().setUp()
        self.db.execute('ALTER TABLE report_artifact_releases ADD COLUMN revoked_by TEXT')
        self.db.execute('ALTER TABLE report_artifact_releases ADD COLUMN state_revision INTEGER DEFAULT 1')
        # New approval plus unrelated month, subject, report type, tenant and scope.
        for rid,subject,period,kind,tenant,scope in (
            (32,SUBJECT,PERIOD,'factsheet',web.TENANT_ID,'internal_shareholder'),
            (33,SUBJECT,'2026-08-31','factsheet',web.TENANT_ID,'internal_shareholder'),
            (34,'other',PERIOD,'factsheet',web.TENANT_ID,'internal_shareholder'),
            (35,SUBJECT,PERIOD,'presentation',web.TENANT_ID,'internal_shareholder'),
            (36,SUBJECT,PERIOD,'factsheet','other','internal_shareholder'),
            (37,SUBJECT,PERIOD,'factsheet',web.TENANT_ID,'public')):
            sid='s'+str(rid)
            self.db.execute('INSERT INTO reporting_snapshots VALUES (?,?,?,?,?,?)',(sid,tenant,subject,period,'frozen','pass'))
            self.db.execute('INSERT INTO report_generation_runs VALUES (?,?,?,?,?)',(rid,tenant,sid,kind,'released_internal'))
            self.db.execute('INSERT INTO report_artifact_releases VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (rid,tenant,21,rid,'internal-reports','private-fixture',DIGEST,scope,None,None,None,1))
        self.db.commit()
    def retire(self):
        return web._supersede_prior_monthly_releases(SQLConnection(self.db),web.TENANT_ID,SUBJECT,PERIOD,'factsheet',32,'owner@example.test','2026-10-11')
    def test_only_previous_same_portfolio_month_type_tenant_scope_is_withdrawn(self):
        self.assertEqual(self.retire(),[{'artifact_release_id':31}])
        rows=self.db.execute('SELECT * FROM report_artifact_releases ORDER BY artifact_release_id').fetchall()
        self.assertEqual(rows[0]['superseded_by_artifact_release_id'],32)
        self.assertEqual(rows[0]['state_revision'],2)
        self.assertTrue(all(r['revoked_at'] is None and r['state_revision']==1 for r in rows[1:]))
        self.assertEqual(self.retire(),[])
    def test_prior_revocation_history_is_preserved(self):
        self.db.execute("UPDATE report_artifact_releases SET revoked_at='original-time',revoked_by='original-owner' WHERE artifact_release_id=31")
        self.retire();row=self.db.execute('SELECT * FROM report_artifact_releases WHERE artifact_release_id=31').fetchone()
        self.assertEqual((row['revoked_at'],row['revoked_by']),('original-time','original-owner'))
    def test_failed_approval_transaction_restores_previous_entitlement(self):
        with self.assertRaisesRegex(RuntimeError,'audit failure'):
            with self.db:
                self.retire()
                raise RuntimeError('audit failure')
        row=self.db.execute('SELECT * FROM report_artifact_releases WHERE artifact_release_id=31').fetchone()
        self.assertIsNone(row['revoked_at']);self.assertIsNone(row['superseded_by_artifact_release_id'])
        self.assertEqual(self.download().body,CONTENT)
    def test_already_superseded_release_is_not_rewritten(self):
        self.db.execute("UPDATE report_artifact_releases SET revoked_at='earlier',superseded_by_artifact_release_id=99 WHERE artifact_release_id=31")
        self.assertEqual(self.retire(),[])
        self.assertEqual(self.db.execute('SELECT superseded_by_artifact_release_id FROM report_artifact_releases WHERE artifact_release_id=31').fetchone()['superseded_by_artifact_release_id'],99)

if __name__=='__main__':unittest.main()
