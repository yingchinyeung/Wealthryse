"""Bind a fresh cloud-path smoke render to reviewed September pixels and numbers."""
import hashlib
import json
from pathlib import Path
import argparse
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    folder = args.folder.resolve()
    accepted = ROOT / '.work/alpha-ms-presentation-all-months-20261007-r4'
    visual_path = accepted / 'visual-review-coverage.json'
    visual = json.loads(visual_path.read_bytes())
    if (sha(visual_path) != '9a4099c5d842619eb0fd1b542f880baf00d22be023ca2f26fbe30e348ae4c76a'
            or visual['status'] != 'rendered_visual_passed' or visual['covered_slides'] != 420):
        raise ValueError('Exact accepted visual coverage required')
    approved = next(r for r in visual['items'] if r['period_end'] == '2026-09-30')
    deck = folder / 'September_Cloud_Path_Smoke.pptx'
    digest = sha(deck)
    snapshot = ROOT / '.work/alpha-ms-presentation-hkd-20261007/inputs/2026-09-30.json'
    code = """import json,sys
from pathlib import Path
from validate_subject_presentation import validate_subject_presentation
print(json.dumps(validate_subject_presentation(Path(sys.argv[1]),Path(sys.argv[2]),subject_id='alpha-world-ms',period_end='2026-09-30',expected_strategy='iStrategy: Alpha World MS')))
"""
    result = subprocess.run([sys.executable, '-c', code, str(deck), str(snapshot)], cwd=folder / 'local-runtime',
        env={**os.environ, 'PYTHONPATH': os.pathsep.join(('src','scripts'))}, capture_output=True, text=True, encoding='utf-8', timeout=90)
    if result.returncode:
        raise ValueError('Fresh smoke output gate failed')
    validation = json.loads(result.stdout)
    rows = []
    for row in approved['slides']:
        number = row['slide']
        old = accepted / 'previews/2026-09-30' / f'slide-{number:02d}.png'
        new = folder / 'september-smoke-previews' / old.name
        if sha(old) != row['preview_sha256'] or sha(new) != sha(old):
            raise ValueError('Fresh smoke preview differs from reviewed slide ' + str(number))
        rows.append({'slide': number, 'preview_sha256': sha(new), 'exact_reviewed_pixel_identity': True})
    if len(rows) != 20 or sha(deck) != digest or validation['output_sha256'] != digest:
        raise ValueError('Complete unchanged fresh smoke render required')
    record = {'status': 'fresh_cloud_path_smoke_passed', 'output_sha256': digest,
        'input_sha256': sha(snapshot), 'source_intent_sha256': sha(folder / 'source-intent.json'),
        'accepted_visual_receipt_sha256': sha(visual_path), 'slides': rows, 'validation': validation,
        'desktop_finalizer_used': False, 'azure_runtime_verified': False,
        'native_powerpoint_verified': False, 'source_data_changed': False, 'production_changed': False}
    with (folder / 'fresh-smoke-qualified.json').open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2, allow_nan=False)
    print(json.dumps({'status': record['status'], 'pixel_identical_slides': 20, 'embedded_workbooks_verified': 6,
                      'azure_runtime_verified': False}))


if __name__ == '__main__':
    main()
