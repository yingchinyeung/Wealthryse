"""One-shot read-only Azure execution of hash-bound T crisis candidate code."""
import os,sys,json,hashlib,base64,zlib,types
from datetime import datetime,timezone
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory

TENANT='00000000-0000-0000-0000-000000000001'


def main():
    bundle=json.loads(zlib.decompress(base64.b64decode(os.environ['T_CRISIS_CANDIDATE_BUNDLE'])))
    import ibkr_etl
    for name,source in bundle['sources'].items():
        assert hashlib.sha256(source.encode()).hexdigest()==bundle['hashes'][name]
        module=types.ModuleType('ibkr_etl.'+name);module.__package__='ibkr_etl'
        sys.modules[module.__name__]=module
        exec(compile(source,name+'.py','exec'),module.__dict__)
    from ibkr_etl.alpha_world_t_crisis import capture_records,from_capture
    results=[]
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,connect_timeout=25,
            options='-c default_transaction_read_only=on -c statement_timeout=60000') as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        raw.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
        assert raw.execute('SHOW transaction_read_only').fetchone()[0]=='on'
        c=PostgresConnection(raw)
        for item in bundle['months']:
            snapshot=item['snapshot']
            try:
                records=capture_records(c,tenant_id=TENANT,holdings_as_of=snapshot['data_as_of'])
            except ValueError:
                diagnostics=c.execute('''SELECT p.account_id,p.snapshot_date,p.conid,
                    (SELECT count(*) FROM flex_rows f WHERE f.tenant_id=? AND f.account_id=p.account_id
                      AND f.row_sha256=p.row_sha256) AS exact_raw_rows,
                    (SELECT count(*) FROM flex_rows f WHERE f.tenant_id=? AND f.account_id=p.account_id
                      AND f.conid=p.conid AND f.report_date=p.snapshot_date) AS dated_raw_rows
                    FROM position_snapshots p JOIN accounts a ON a.account_id=p.account_id
                    WHERE a.tenant_id=? AND p.account_id IN ('U16088571','U16088606') AND p.snapshot_date=?
                    AND NOT EXISTS (SELECT 1 FROM crisis_replay_components m WHERE m.tenant_id=?
                      AND m.subject_id=p.account_id AND m.holdings_as_of=p.snapshot_date AND m.conid=p.conid)''',
                    (TENANT,TENANT,TENANT,snapshot['data_as_of'],TENANT)).fetchall()
                print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'diagnostic',
                    'unmapped_positions':[dict(r) for r in diagnostics]},default=str),flush=True)
                raise
            replay=from_capture(records,snapshot)
            assert abs(replay['maximum_drawdown']-item['maximum_drawdown'])<1e-12
            assert replay['proxy_components']==item['proxy_components']
            results.append({'period_end':snapshot['period_end'],'maximum_drawdown':replay['maximum_drawdown'],
                'proxy_components':replay['proxy_components'],'qualification_status':replay['qualification_status'],
                'observations':replay['observations'],'live_input_sha256':replay['input_sha256']})
        raw.rollback()
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'passed','captured_at':datetime.now(timezone.utc).isoformat(),
        'months':len(results),'results':results,'candidate_code_hashes':bundle['hashes'],
        'database_read_only':True,'production_changed':False,'reports_released':False}),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'failed','error_type':type(error).__name__,
            'error':str(error) if isinstance(error,(ValueError,KeyError,AssertionError)) else 'Service details redacted'}),flush=True)
        raise SystemExit(1)
