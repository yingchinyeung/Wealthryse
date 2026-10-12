"""Recover only scoped source files from the already-scanned immutable image."""
import hashlib
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[1]
SCAN=ROOT/'.work/alpha-ms-headers-v14r15-20261008-r2/web/scan-v1'
OUTPUT=ROOT/'.work/alpha-ms-crisis-release-20261008/web-baseline-v1'
IMAGE_SHA='ea6d337314269593c8bf214b22dab756ad367b3a65840814c0c65031377d6a70'
NAMES=('src/ibkr_etl/alpha_world_ms_presentation.py','scripts/render_monthly_presentation.mjs',
       'scripts/audit_alpha_ms_presentation.py','src/ibkr_etl/agent_tools.py',
       'src/ibkr_etl/reporting_snapshot.py','src/ibkr_etl/presentation_design.py','scripts/release_internal_reports.py')

def sha(data):return hashlib.sha256(data).hexdigest()

def main():
    if OUTPUT.exists():raise ValueError('Preserve previous source capture')
    raw=(SCAN/IMAGE_SHA).read_bytes();assert sha(raw)==IMAGE_SHA
    oci=json.loads(raw);config_sha=oci['config']['digest'].split(':')[1]
    config_raw=(SCAN/config_sha).read_bytes();assert sha(config_raw)==config_sha
    config=json.loads(config_raw);sources={}
    with tarfile.open(SCAN/'image.docker.tar') as outer:
        manifest=json.load(outer.extractfile('manifest.json'))[0]
        assert sha(outer.extractfile(manifest['Config']).read())==config_sha
        assert len(manifest['Layers'])==len(config['rootfs']['diff_ids'])
        for name,digest in zip(manifest['Layers'],config['rootfs']['diff_ids']):
            h=hashlib.sha256()
            with outer.extractfile(name) as stream:
                while part:=stream.read(1024*1024):h.update(part)
            assert 'sha256:'+h.hexdigest()==digest
            with tarfile.open(fileobj=outer.extractfile(name),mode='r|') as layer:
                for entry in layer:
                    key=entry.name.removeprefix('./').removeprefix('/')
                    if key in {'app/'+n for n in NAMES}:
                        assert entry.isfile() and 0<entry.size<2*1024*1024
                        sources[key.removeprefix('app/')]=layer.extractfile(entry).read()
    assert set(sources)==set(NAMES)
    OUTPUT.mkdir()
    for name,data in sources.items():
        p=OUTPUT/name;p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('xb') as stream:stream.write(data)
    proof={'image_digest':'sha256:'+IMAGE_SHA,'config_digest':'sha256:'+config_sha,
        'all_uncompressed_layer_digests_verified':True,'files_sha256':{n:sha(d) for n,d in sources.items()},
        'network_requests':0,'source_data_changed':False}
    with (OUTPUT/'verification.json').open('x',encoding='utf-8') as stream:json.dump(proof,stream,indent=2)
    print(json.dumps(proof))

if __name__=='__main__':main()
