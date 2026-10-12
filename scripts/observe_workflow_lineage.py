"""Read-only active-account lineage observation; never fetches IBKR/provider data."""
import argparse,json,os,sys
from datetime import date,datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ibkr_etl.workflow_lineage import inspect_lineage
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--as-of',required=True);args=p.parse_args(argv)
    cutoff=date.fromisoformat(args.as_of)
    if cutoff>datetime.now(timezone.utc).date():p.error('Future cutoff is not allowed')
    import psycopg
    tenant=os.environ['PORTFOLIO_TENANT_ID']
    with psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=20,
            options='-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=5000',
            row_factory=_hybrid_row_factory) as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        raw.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
        try:
            result=inspect_lineage(PostgresConnection(raw),tenant_id=tenant,as_of=args.as_of)
        except (ValueError,TypeError,KeyError) as error:
            result={'probe_complete':False,'status':'blocked','error_type':type(error).__name__,
                'reason':str(error) if isinstance(error,ValueError) else 'Invalid lineage record',
                'execution_authorized':False}
        else:
            result.update(probe_complete=True,status='observed',database_read_only=True)
        raw.rollback()
    print('WORKFLOW_LINEAGE_OBSERVATION '+json.dumps(result,sort_keys=True,allow_nan=False),flush=True)
    return 0 if result.get('probe_complete') and result['observed_coverage_verified'] else 1


if __name__=='__main__':raise SystemExit(main())
