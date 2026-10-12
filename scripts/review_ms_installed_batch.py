"""Render and compare every installed-worker output to accepted cloud pixels."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess

import review_ms_cloud_visuals as visual
import deploy_ms_presentation_worker_release as release


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(); p.add_argument('--folder', type=Path, required=True)
    args = p.parse_args(); folder = args.folder.resolve(); stage = folder / 'registered-batch-v1'
    qualified = release.read(stage, 'render-qualified')
    acceptance = release.read(folder / 'runtime-v1', 'candidate-render-acceptance')
    if qualified['status'] != 'installed_private_render_readback_passed' or acceptance['total_slides_visually_covered'] != 420:
        raise ValueError('Complete installed render and accepted reference required')
    proof_sha = sha(stage / 'render-qualified.json'); accepted_sha = sha(folder / 'runtime-v1/candidate-render-acceptance.json')
    previous_path=folder/'runtime-v1/cloud-visual-v1/receipt.json'
    previous=json.loads(previous_path.read_bytes());previous_items={r['period_end']:r for r in previous['items']}
    if folder.name.startswith('alpha-ms-format-worker-candidate-'):
        reference = acceptance['protected_evidence_receipts']['cloud_visual']
        digest = hashlib.sha256(json.dumps(previous, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if (reference['sha256'] != digest or reference['protected_readback_verified'] is not True
                or not reference['version_id'] or previous['image'] != qualified['image']
                or previous['runtime_qualified_sha256'] != acceptance['candidate_qualified_sha256']
                or previous['matched_reviewed_slides'] != 420 or previous['different_slides'] != 0):
            raise ValueError('Protected format candidate cloud visual evidence changed')
    elif sha(previous_path)!='0782c79119292b7218f1f39e1f8440804b1e9894b99a81947c482d2f7d0cdc3c':
        raise ValueError('Previously accepted cloud visual evidence changed')
    output = stage / 'visual-v1'; output.mkdir(exist_ok=False)
    exporter = visual.ROOT / 'scripts/inspect_alpha_ms_presentation.mjs'; exporter_sha = sha(exporter)
    if exporter_sha!=previous['preview_exporter_sha256']:raise ValueError('Preview exporter changed')
    env = {**os.environ, 'RUNTIME_NODE_MODULES': visual.MODULES, 'PYTHONUTF8': '1'}

    def check(row):
        period = row['period_end']; target = output / period; target.mkdir()
        pdf = stage / 'outputs' / Path(row['factsheet_blob']).name
        pptx = stage / 'outputs' / Path(row['presentation_blob']).name
        if sha(pdf) != row['factsheet_sha256'] or sha(pptx) != row['presentation_sha256']:
            raise ValueError('Downloaded file changed')
        result = subprocess.run([str(visual.NODE), str(exporter), str(pptx), str(target / 'slides')],
            cwd=visual.ROOT, env=env, capture_output=True, text=True, encoding='utf-8', timeout=240)
        visual.save(target / 'slide-export.json', {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        if result.returncode or len(list((target / 'slides').glob('slide-*.png'))) != 20:
            raise ValueError('All 20 slide previews required')
        pdf_dir = target / 'pdf'; pdf_dir.mkdir()
        result = subprocess.run([str(visual.POPPLER), '-png', '-r', '120', str(pdf), str(pdf_dir / 'page')],
            cwd=visual.ROOT, env=env, capture_output=True, text=True, timeout=90)
        if result.returncode or len(list(pdf_dir.glob('page-*.png'))) != 2: raise ValueError('Both PDF previews required')
        references = folder / 'runtime-v1/cloud-visual-v1' / period
        accepted_previews=previous_items[period]
        expected_slides={f"slide-{r['slide']:02d}.png":r['preview_sha256']
            for r in accepted_previews['matched_slides']+accepted_previews['different_slides']}
        expected_pdf={Path(r['path']).name:r['sha256'] for r in accepted_previews['pdf_previews']}
        items = []
        for kind, names in [('slides', [f'slide-{n:02d}.png' for n in range(1, 21)]), ('pdf', ['page-1.png', 'page-2.png'])]:
            for name in names:
                old = references / kind / name; new = target / kind / name
                if sha(old)!=(expected_slides if kind=='slides' else expected_pdf)[name]:
                    raise ValueError('Accepted preview reference changed')
                items.append({'kind': kind, 'name': name, 'new_sha256': sha(new),
                              'accepted_sha256': sha(old), 'identical': sha(new) == sha(old)})
        if sha(pdf) != row['factsheet_sha256'] or sha(pptx) != row['presentation_sha256']: raise ValueError('Source changed during preview')
        record = {'period_end': period, 'pdf_sha256': sha(pdf), 'presentation_sha256': sha(pptx),
                  'previews': items, 'differences': [r for r in items if not r['identical']]}
        visual.save(target / 'comparison.json', record)
        return record

    items, errors = [], []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(check, row): row['period_end'] for row in qualified['proof']['artifacts']}
        for future in as_completed(futures):
            try:
                item = future.result(); items.append(item)
                print(json.dumps({'period': item['period_end'], 'preview_differences': len(item['differences'])}), flush=True)
            except Exception as error:
                errors.append({'period': futures[future], 'error': str(error) if isinstance(error, ValueError) else type(error).__name__})
    require_unchanged = sha(stage / 'render-qualified.json') == proof_sha and sha(exporter) == exporter_sha
    if not require_unchanged: raise ValueError('Frozen inspection inputs changed')
    differences = sum(len(i['differences']) for i in items)
    record = {'status': 'accepted_pixel_identity_passed' if len(items) == 21 and not errors and not differences else 'review_required',
              'render_qualified_sha256': proof_sha, 'accepted_visual_sha256': accepted_sha,
              'months': len(items), 'slides_rendered': len(items)*20, 'pdf_pages_rendered': len(items)*2,
              'differences': differences, 'errors': errors, 'items': sorted(items, key=lambda i: i['period_end']),
              'native_microsoft_powerpoint_tested': False, 'database_writes': 0}
    visual.save(output / 'receipt.json', record)
    print(json.dumps({k: record[k] for k in ('status', 'months', 'slides_rendered', 'pdf_pages_rendered', 'differences', 'errors')}), flush=True)
    if record['status'] != 'accepted_pixel_identity_passed': raise SystemExit(1)


if __name__ == '__main__': main()
