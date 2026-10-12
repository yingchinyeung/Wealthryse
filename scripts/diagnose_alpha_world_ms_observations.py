"""Scoped read-only source-date diagnostic, executed inside the reporting job."""
import json
import os
import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

TENANT = "00000000-0000-0000-0000-000000000001"
with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=60000", connect_timeout=25) as c:
    c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    c.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
    rows=c.execute("""SELECT subject_id,snapshot_date,currency,total_text FROM subject_nav_series
        WHERE tenant_id=%s AND subject_id IN ('alpha-world-ms','hong-kong-china','U16088571','U16088606')
        AND snapshot_date BETWEEN '2026-08-26' AND '2026-08-31' ORDER BY subject_id,snapshot_date,currency""",(TENANT,)).fetchall()
    positions=c.execute("""SELECT p.account_id,p.snapshot_date,count(*) AS positions FROM position_snapshots p
        JOIN accounts a ON a.account_id=p.account_id WHERE a.tenant_id=%s
        AND p.account_id IN ('U16088571','U16088606') AND p.snapshot_date BETWEEN '2026-08-26' AND '2026-08-31'
        GROUP BY p.account_id,p.snapshot_date ORDER BY p.account_id,p.snapshot_date""",(TENANT,)).fetchall()
    coverage=c.execute("""SELECT subject_id,snapshot_date,currency FROM subject_nav_series WHERE tenant_id=%s
        AND subject_id IN ('alpha-world-ms','hong-kong-china','U16088571','U16088606')
        AND snapshot_date BETWEEN '2025-01-20' AND '2026-09-30' ORDER BY subject_id,snapshot_date""",(TENANT,)).fetchall()
    c.rollback()
result={"mode":"read_only","source_data_changed":False,"nav":rows,"positions":positions,"coverage":coverage}
service=BlobServiceClient(os.environ["AZURE_STORAGE_ACCOUNT_URL"],credential=DefaultAzureCredential())
service.get_blob_client("internal-reports","review/alpha-world-ms/v14-20261007/source-date-diagnostic.json").upload_blob(
    json.dumps(result,default=str,sort_keys=True).encode(),overwrite=False)
print(json.dumps({"status":"source_diagnostic_saved","source_data_changed":False,"rows":len(rows)}),flush=True)
