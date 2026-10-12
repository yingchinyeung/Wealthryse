"""Private reviewed batch with S&P500 caption and confirmed NAV-after-interest title."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ibkr_etl.monthly_artifacts import render_factsheet, ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION

CAPTURE = ROOT / '.work/alpha-ms-factsheets-v13-20261007'
RECOVERY = ROOT / '.work/alpha-ms-recovery-20261007/corrected-inputs'
OUTPUT = ROOT / 'output/pdf/AlphaWorld_MS_Monthly_Factsheets_v14r7'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def main():
    capture = json.loads((CAPTURE / 'capture.json').read_bytes())
    corrected = {r['period_end']: r for r in json.loads((ROOT / 'output/pdf/AlphaWorld_MS_Recovered_20261007/manifest.json').read_bytes())['artifacts']}
    OUTPUT.mkdir(exist_ok=True)
    for asset in capture['assets']:
        if sha((CAPTURE / (asset['asset_role'] + '.pdf')).read_bytes()) != asset['sha256']:
            raise ValueError('Design authority changed')
    artifacts = []
    for row in capture['snapshots']:
        period = row['period_end']
        data = ((RECOVERY / (period + '.json')) if period in corrected else (CAPTURE / 'snapshots' / (period + '.json'))).read_bytes()
        expected = corrected[period]['data_sha256'] if period in corrected else row['data_sha256']
        if sha(data) != expected:
            raise ValueError('Historical source input changed')
        payload = json.loads(data)
        before = json.dumps(payload, sort_keys=True, allow_nan=False)
        path = OUTPUT / ('AlphaWorld_MS_Factsheet_' + period + '.pdf')
        if path.exists():
            raise FileExistsError('Reviewed output already exists')
        result = render_factsheet(payload, template_path=CAPTURE/'template.pdf', design_model_path=CAPTURE/'completed_example.pdf', output_path=path)
        if before != json.dumps(payload, sort_keys=True, allow_nan=False):
            raise ValueError('Renderer mutated the input')
        artifacts.append({'period_end': period, 'data_sha256': expected, 'sha256': sha(path.read_bytes()), 'render': result})
        print(json.dumps({'rendered': period}), flush=True)
    (OUTPUT/'manifest.json').write_text(json.dumps({'count':len(artifacts), 'layout_version':ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION, 'artifacts':artifacts, 'source_data_changed':False, 'reports_released':False},indent=2),encoding='utf-8')

if __name__ == '__main__':
    main()
