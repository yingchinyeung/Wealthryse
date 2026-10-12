"""Sanitized isolated ACR source context: code, synthetic tests and pinned JWT.

No data, reports, templates, environment files, credentials or .git included.
The evidence registry is hashed separately to avoid self-referential enrollment.
"""
import hashlib
import json
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/specialist-gateway-20261002/context'


def main():
    files={str(p.relative_to(ROOT)).replace('\\','/'):p.read_bytes()
        for p in (ROOT/'src/ibkr_etl').rglob('*.py')}
    files['scripts/serve_specialist_gateway.py']=(ROOT/'scripts/serve_specialist_gateway.py').read_bytes()
    hashes={name:hashlib.sha256(payload).hexdigest() for name,payload in sorted(files.items())}
    registry=hashes.pop('src/ibkr_etl/specialist_evidence.py')
    digest=hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    # Refuse building any executable observation producer before enrollment.
    from ibkr_etl.specialist_evidence import OBSERVATION_MANIFESTS
    print(json.dumps(dict(source_manifest_sha256=digest,registry_sha256=registry,files=len(files))),flush=True)
    if digest not in OBSERVATION_MANIFESTS: raise ValueError('Review/enroll this manifest before packaging')
    FOLDER.mkdir(parents=True,exist_ok=True)
    for name,payload in files.items():
        target=FOLDER/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload)
    for p in (ROOT/'tests').glob('test_agent*.py'):
        target=FOLDER/'tests'/p.name;target.parent.mkdir(exist_ok=True);shutil.copyfile(p,target)
    # Full locked package block, no resolver or unbounded "latest" dependency.
    lock=(ROOT/'requirements.lock').read_text(encoding='utf-8')
    block=lock.split('pyjwt==2.15.1',1)[1].split('\n    #',1)[0]
    (FOLDER/'gateway-requirements.lock').write_text('pyjwt==2.15.1'+block+'\n',encoding='utf-8')
    manifest=dict(source_manifest_sha256=digest,registry_sha256=registry,files=hashes,
        financial_data_included=False,build_scope='isolated-specialist-observation-gateway')
    (FOLDER/'gateway-build.json').write_text(json.dumps(manifest,sort_keys=True),encoding='utf-8')
    # Existing Dockerfile is a reviewed literal, copied without regeneration.
    shutil.copyfile(ROOT/'deploy/specialist-gateway.Dockerfile',FOLDER/'Dockerfile')


if __name__=='__main__':main()
