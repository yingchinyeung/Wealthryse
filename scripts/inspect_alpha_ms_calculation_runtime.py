"""Read-only runtime signatures, source hashes and safe failure logs."""
import hashlib
import inspect
import json
import os
import sys
sys.path[:0] = ['/app/src', '/app/scripts']
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from ibkr_etl.derived_portfolio import refresh_filtered_portfolio, refresh_fixed_unit_composite
functions = (refresh_filtered_portfolio, refresh_fixed_unit_composite)
result = {'status':'runtime_inspected','functions':[
    {'name': f.__name__, 'signature': str(inspect.signature(f)),
     'source_sha256':hashlib.sha256(inspect.getsource(f).encode()).hexdigest()} for f in functions],
    'database_writes':0}
BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential()).get_blob_client(
    'internal-reports','review/alpha-world-ms/source-recovery-20261007/calculation-runtime.json').upload_blob(
        json.dumps(result,sort_keys=True).encode(),overwrite=False)
print('ALPHA_MS_RECOVERY_RESULT '+json.dumps(result),flush=True)
