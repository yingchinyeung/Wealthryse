"""Locally qualify the shared MS renderer against all 21 pinned HKD inputs."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from ibkr_etl.monthly_artifacts import render_factsheet
from ibkr_etl.alpha_world_ms_copy import COPY_VERSION, validate_copy


def main():
    source = ROOT/'.work/alpha-ms-hkd-hibor-20261007/hkd-inputs'
    capture = ROOT/'.work/alpha-ms-factsheets-v13-20261007'
    scratch = ROOT/'tmp/pdfs/alpha-ms-copy-v14r12/shared-renderer'
    scratch.mkdir(parents=True, exist_ok=False)
    assets = json.loads((capture/'capture.json').read_bytes())['assets']
    for asset in assets:
        assert hashlib.sha256((capture/(asset['asset_role']+'.pdf')).read_bytes()).hexdigest() == asset['sha256']
    rows = []
    for path in sorted(source.glob('*.json')):
        raw = path.read_bytes(); snapshot = json.loads(raw)
        frozen = json.dumps(snapshot, sort_keys=True)
        target = scratch/(path.stem+'.pdf')
        result = render_factsheet(snapshot, template_path=capture/'template.pdf',
                                 design_model_path=capture/'completed_example.pdf', output_path=target)
        assert raw == path.read_bytes() and frozen == json.dumps(snapshot, sort_keys=True)
        assert result['pdf_accuracy_validation']['status'] == 'pass'
        assert validate_copy(target)['status'] == 'pass'
        rows.append({'month': path.stem, 'input_sha256': hashlib.sha256(raw).hexdigest(),
                     'input_unchanged': True, 'copy_check': 'pass', 'financial_chart_accuracy_check': 'pass'})
        print(path.stem+' passed', flush=True)
    assert len(rows) == 21
    for asset in assets:
        assert hashlib.sha256((capture/(asset['asset_role']+'.pdf')).read_bytes()).hexdigest() == asset['sha256']
    record = {'version': COPY_VERSION, 'months_passed': len(rows), 'production_changed': False,
              'approved_design_assets_unchanged': True, 'months': rows}
    (scratch/'qualification.json').write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
    folder = ROOT/'output/pdf/AlphaWorld_MS_Text_Corrections_v14r12'
    manifest = json.loads((folder/'manifest.json').read_text())
    manifest['shared_renderer_qualification'] = record
    (folder/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__': main()
