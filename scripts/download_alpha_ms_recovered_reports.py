"""Read back cloud originals and corrected private report artifacts unchanged."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.work/alpha-ms-azure-controller-runtime'))
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    prefix = 'review/alpha-world-ms/source-recovery-20261007/'
    service = BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    folder = ROOT / 'output/pdf/AlphaWorld_MS_Recovered_20261007'
    inputs = ROOT / '.work/alpha-ms-recovery-20261007/corrected-inputs'
    folder.mkdir(parents=True, exist_ok=True)
    inputs.mkdir(parents=True, exist_ok=True)
    def get(name, expected=None):
        blob = service.get_blob_client('internal-reports', prefix + name)
        data = blob.download_blob().readall()
        digest = sha(data)
        if expected is not None and digest != expected:
            raise ValueError('Artifact digest differs from its private manifest')
        if blob.get_blob_properties().metadata.get('sha256') != digest:
            raise ValueError('Cloud content integrity metadata differs')
        return data
    def save(path, data):
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError('Local artifact exists with different bytes')
        else:
            with path.open('xb') as f:
                f.write(data)
    raw = get('corrected-reports/manifest.json')
    manifest = json.loads(raw)
    if manifest['subject_id'] != 'alpha-world-ms' or len(manifest['artifacts']) != 2:
        raise ValueError('Unexpected corrected report scope')
    save(folder / 'manifest.json', raw)
    for entry in manifest['artifacts']:
        day = entry['period_end']
        if day not in ('2026-08-31','2026-09-30'):
            raise ValueError('Unexpected corrected reporting month')
        name = 'AlphaWorld_MS_Factsheet_' + day + '_Recovered.pdf'
        if entry['blob_name'] != prefix + 'corrected-reports/' + name:
            raise ValueError('Unexpected report path')
        save(folder / name, get('corrected-reports/' + name, entry['sha256']))
        save(inputs / (day + '.json'), get('corrected-inputs/' + day + '.json', entry['data_sha256']))
    print(json.dumps({'status':'azure_corrected_artifacts_readback_verified','count':2,'folder':str(folder)}))


if __name__ == '__main__':
    main()
