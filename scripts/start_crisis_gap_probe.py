"""Dispatch only the fixed-scope read-only crisis preflight."""
import base64
import copy
import hashlib
import json
from pathlib import Path
from audit_azure_runtime import az,RG
from deploy_metadata_monitor import request


def main():
    if az('account','show','--query','id') != '3956e821-cee4-403f-8708-e84e2ed490eb':
        raise RuntimeError('Unexpected subscription')
    job=az('containerapp','job','show','-g',RG,'-n','ibkrv2stg-risk-snapshots')
    template=copy.deepcopy(job['properties']['template'])
    if template.get('volumes') or len(template['containers']) != 1 or job['properties']['configuration']['replicaRetryLimit'] != 0:
        raise RuntimeError('Unexpected diagnostic configuration')
    template.pop('volumes',None)
    source=Path(__file__).with_name('inspect_crisis_gap.py').read_bytes()
    encoded=base64.b64encode(source).decode()
    template['containers'][0]['command']=['python']
    template['containers'][0]['args']=['-c',f"import base64;exec(compile(base64.b64decode('{encoded}'),'crisis-gap','exec'))"]
    result=request('POST',job['id']+'/start?api-version=2024-03-01',data=template)
    print(json.dumps({'execution':result['name'],'source_sha256':hashlib.sha256(source).hexdigest()}))


if __name__ == '__main__':
    main()
