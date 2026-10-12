"""Read-only artifact identities for verifying real website downloads."""
import json,os,sys
from pathlib import Path
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=30000'
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl.db import connect
from ibkr_etl.reporting_snapshot import active_month_end_snapshot_id

def main():
    tenant=os.environ['PORTFOLIO_TENANT_ID']
    conn=connect(Path('/tmp/unused.sqlite3'),os.environ['DATABASE_URL'],tenant)
    records=[]
    try:
        for subject in ('alpha-world-ms','alpha-world-t','hong-kong-china','U16088571','U16088606'):
            snapshot=active_month_end_snapshot_id(conn,tenant_id=tenant,subject_id=subject,period_end='2026-09-30')
            if not snapshot:raise ValueError('Current frozen snapshot missing')
            rows=conn.execute('''SELECT generation_run_id,report_type,status,output_reference,output_sha256
                FROM report_generation_runs WHERE tenant_id=? AND snapshot_id=CAST(? AS uuid)
                AND report_type IN ('factsheet','presentation') ORDER BY generation_run_id DESC''',(tenant,snapshot)).fetchall()
            selected={}
            for row in rows:selected.setdefault(str(row['report_type']),dict(row))
            if set(selected)!={'factsheet','presentation'}:raise ValueError('Incomplete package')
            records.append({'subject':subject,'period':'2026-09-30','artifacts':selected})
    finally:conn.close()
    print('PLATFORM_CORRECTION_PROBE '+json.dumps({'read_only':True,'database_writes':0,'records':records}),flush=True)

if __name__=='__main__':main()
