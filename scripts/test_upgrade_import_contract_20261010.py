"""Preserve the installed pilot-import implementation using synthetic I/O."""
import contextlib,io,json,os,runpy,unittest
from pathlib import Path
from unittest.mock import MagicMock,Mock,patch

class ImportContract(unittest.TestCase):
    def setUp(self):
        self.main=runpy.run_path('/app/scripts/import_pilot_snapshot.py',run_name='qualification_import')['main'];self.g=self.main.__globals__
        self.env={'DATABASE_URL':'postgresql://synthetic.invalid/test','PILOT_ACCOUNT_ID':'test-account','PILOT_START_DATE':'2025-01-20',
            'AZURE_STORAGE_ACCOUNT_URL':'https://synthetic.invalid','PILOT_SNAPSHOT_BLOB':'synthetic.sqlite','PORTFOLIO_TENANT_ID':'test-tenant'}
    def case(self,fail=None,existing=False):
        env=dict(self.env)
        if fail in ('DATABASE_URL','AZURE_STORAGE_ACCOUNT_URL','PILOT_SNAPSHOT_BLOB'):env[fail]=''
        tables=self.g['ACCOUNT_TABLES'];counts={t:1 for t in tables};coverage={t:{'first':'2025-01-20','last':'2026-09-30'} for t in tables}
        download=Mock(return_value=fail!='missing_blob');validate=Mock(return_value={'row_counts':counts,'coverage':coverage})
        if fail=='validation':validate.side_effect=RuntimeError('synthetic invalid snapshot')
        conn=MagicMock();conn.__enter__.return_value=conn;upsert=Mock(return_value=1);queries=[]
        if fail=='upsert':upsert.side_effect=RuntimeError('synthetic import failure')
        def execute(sql,args=()):
            q=' '.join(sql.split());queries.append((q,args));result=Mock()
            if q.startswith('SELECT run_id'):result.fetchone.return_value={'run_id':4} if existing else None
            elif q.startswith('INSERT INTO ingestion_runs'):result.fetchone.return_value={'run_id':5}
            elif q.startswith('SELECT COUNT'):result.fetchone.return_value={'n':2 if fail=='reconcile' else 1}
            elif not q.startswith(('SELECT set_config','UPDATE ingestion_runs')):raise AssertionError(q)
            return result
        conn.execute.side_effect=execute;source=Mock()
        def source_execute(sql):
            if sql=='SELECT * FROM accounts':return Mock(fetchone=Mock(return_value={'account_id':'test-account'}))
            return [{'synthetic':1}]
        source.execute.side_effect=source_execute;output=io.StringIO()
        with patch.dict(os.environ,env,clear=True),patch.dict(self.g,download_file_if_exists=download,validate_pilot_snapshot=validate,_sha256=Mock(return_value='a'*64),_upsert=upsert),patch('psycopg.connect',return_value=conn) as db,patch('sqlite3.connect',return_value=source) as local,contextlib.redirect_stdout(output):
            if fail:
                with self.assertRaises(RuntimeError):self.main()
            else:self.assertEqual(self.main(),0)
        if fail in ('DATABASE_URL','AZURE_STORAGE_ACCOUNT_URL','PILOT_SNAPSHOT_BLOB','missing_blob','validation'):db.assert_not_called();local.assert_not_called();return
        self.assertEqual(queries[0][1],('test-tenant',))
        if existing:
            local.assert_not_called();upsert.assert_not_called();conn.commit.assert_not_called();self.assertEqual(json.loads(output.getvalue())['result'],'already_imported');return
        self.assertTrue(local.call_args.args[0].endswith('?mode=ro'));self.assertIs(local.call_args.kwargs['uri'],True);source.close.assert_called_once()
        self.assertEqual(conn.commit.call_count,2)
        if fail:
            conn.rollback.assert_called_once();self.assertTrue(any("status = 'failed'" in q for q,_ in queries))
        else:
            conn.rollback.assert_not_called();self.assertTrue(any("status = 'success'" in q for q,_ in queries))
            self.assertEqual(upsert.call_args_list[0].args[2][0]['tenant_id'],'test-tenant')
            self.assertTrue(all(call.args[2][0]['last_seen_run_id']==5 for call in upsert.call_args_list[2:]))
            self.assertEqual(json.loads(output.getvalue())['status'],'PASS')
    def test_success(self):self.case()
    def test_idempotent_import(self):self.case(existing=True)
    def test_missing_database(self):self.case('DATABASE_URL')
    def test_missing_storage(self):self.case('AZURE_STORAGE_ACCOUNT_URL')
    def test_missing_reference(self):self.case('PILOT_SNAPSHOT_BLOB')
    def test_missing_blob(self):self.case('missing_blob')
    def test_validation_failure(self):self.case('validation')
    def test_upsert_failure(self):self.case('upsert')
    def test_reconciliation_failure(self):self.case('reconcile')
    def test_empty_upsert(self):
        conn=Mock();self.assertEqual(self.g['_upsert'](conn,'test',[],('id',)),0);conn.cursor.assert_not_called()
    def test_parameterized_upsert(self):
        conn=MagicMock();rows=[{'id':1,'amount':2},{'id':3,'amount':4}]
        self.assertEqual(self.g['_upsert'](conn,'synthetic_table',rows,('id',)),2)
        call=conn.cursor.return_value.__enter__.return_value.executemany.call_args
        self.assertEqual(call.args[1],[[1,2],[3,4]])
        query=call.args[0].as_string();self.assertIn('"synthetic_table"',query);self.assertIn('VALUES (%s, %s)',query)
    def test_required_strips_spaces(self):
        with patch.dict(os.environ,{'SYNTHETIC':' value '},clear=True):self.assertEqual(self.g['_required']('SYNTHETIC'),'value')

if __name__=='__main__':unittest.main()
