"""Render downloaded MS cloud outputs; verify exact reviewed slide pixel identity.

PDF page previews are evidence for human inspection, not automatic visual approval.
Never modifies downloaded artifacts or registers/releases reports.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NODE = Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
MODULES = 'C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
POPPLER = Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe')
APPROVED = ROOT / '.work/alpha-ms-presentation-all-months-20261007-r4'
VISUAL_SHA = '9a4099c5d842619eb0fd1b542f880baf00d22be023ca2f26fbe30e348ae4c76a'
FORMAT_REVIEW_SHA = 'a217a02c02177fe9f7ab54026aff13829974d25877f541e4657acedb17ac9aa9'
FORMAT_PREVIEWS_SHA = '382c7db1ff51021765b7bdb15dfe20ca70f4bf7e37ec2417ca3ff55027b2cd65'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def reviewed_reference(folder):
    if not folder.name.startswith('alpha-ms-format-worker-candidate-'):
        visual_path = APPROVED / 'visual-review-coverage.json'
        approved = json.loads(visual_path.read_bytes())
        if sha(visual_path) != VISUAL_SHA or approved['status'] != 'rendered_visual_passed' or approved['covered_slides'] != 420:
            raise ValueError('Exact complete reviewed reference required')
        return APPROVED / 'previews', approved['items'], VISUAL_SHA
    baseline = ROOT / '.work/alpha-ms-number-formats-20261008-v2'
    review_path = baseline / 'review-acceptance.json'
    previews_path = baseline / 'all-month-previews/comparison.json'
    review, previews = json.loads(review_path.read_bytes()), json.loads(previews_path.read_bytes())
    if (sha(review_path) != FORMAT_REVIEW_SHA or sha(previews_path) != FORMAT_PREVIEWS_SHA
            or review['status'] != 'private_all_month_format_financial_and_visual_delta_review_passed'
            or review['months'] != 21 or review['slides_rendered'] != 420
            or review['preview_comparison_sha256'] != sha(previews_path)):
        raise ValueError('Exact complete reviewed format reference required')
    accepted = {r['period_end']: r for r in review['items']}
    items = []
    for row in previews['items']:
        if (row['presentation_sha256'] != accepted[row['period_end']]['presentation_sha256']
                or len(row['previews']) != 20
                or {s['slide'] for s in row['previews']} != set(range(1,21))):
            raise ValueError('Incomplete reviewed format preview coverage')
        items.append({'period_end': row['period_end'], 'slides': [
            {'slide': s['slide'], 'preview_sha256': s['new_sha256']} for s in row['previews']]})
    if len(items) != 21 or len({r['period_end'] for r in items}) != 21:
        raise ValueError('Unique complete format reference months required')
    return baseline / 'all-month-previews', items, FORMAT_REVIEW_SHA


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    folder = args.folder.resolve()
    runtime = folder / 'runtime-v1'
    proof_path = runtime / 'candidate-qualified.json'
    proof_sha = sha(proof_path)
    proof = json.loads(proof_path.read_bytes())
    if proof['status'] != 'passed' or proof['proof']['count'] != 21:
        raise ValueError('Complete qualified cloud runtime required')
    preview_root, reviewed_items, reference_sha = reviewed_reference(folder)
    references = {r['period_end']: r for r in reviewed_items}
    prior_path = ROOT / '.work/alpha-ms-presentation-worker-candidate-20261007-r2/runtime-v1/candidate-qualified.json'
    prior = json.loads(prior_path.read_bytes())
    if prior['status'] != 'passed' or prior['proof']['count'] != 21:
        raise ValueError('Prior verified factsheet baseline required')
    prior_pdfs = {r['period_end']: r['factsheet_sha256'] for r in prior['proof']['artifacts']}
    stage = runtime / 'cloud-visual-v1'
    stage.mkdir(exist_ok=False)
    script_path = ROOT / 'scripts/inspect_alpha_ms_presentation.mjs'
    script_sha = sha(script_path)
    env = {**os.environ, 'RUNTIME_NODE_MODULES': MODULES, 'PYTHONUTF8': '1'}

    def check(row):
        date = row['period_end']
        pptx = runtime / 'candidate-outputs' / ('AlphaWorld_MS_Presentation_' + date + '.pptx')
        pdf = runtime / 'candidate-outputs' / ('AlphaWorld_MS_Factsheet_' + date + '.pdf')
        if sha(pptx) != row['presentation_sha256'] or sha(pdf) != row['factsheet_sha256']:
            raise ValueError('Downloaded output differs')
        output = stage / date
        output.mkdir()
        command = [str(NODE), str(script_path), str(pptx), str(output / 'slides')]
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', timeout=240)
        save(output / 'preview-export.json', {'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        if result.returncode or len(list((output / 'slides').glob('slide-*.png'))) != 20:
            raise ValueError('Full cloud slide export failed')
        matched, different = [], []
        for ref in references[date]['slides']:
            number = ref['slide']
            old = preview_root / date / f'slide-{number:02d}.png'
            new = output / 'slides' / old.name
            if sha(old) != ref['preview_sha256']:
                raise ValueError('Reviewed reference changed')
            item = {'slide': number, 'preview_sha256': sha(new), 'reviewed_reference_sha256': sha(old)}
            (matched if sha(new) == sha(old) else different).append(item)
        if len(matched) + len(different) != 20:
            raise ValueError('Complete all-slide comparison required')
        # Render every PDF page without altering its numerical/report content.
        pdf_dir = output / 'pdf'
        pdf_dir.mkdir()
        result = subprocess.run([str(POPPLER), '-png', '-r', '120', str(pdf), str(pdf_dir / 'page')],
                                env=env, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=90)
        pages = sorted(pdf_dir.glob('page-*.png'))
        if result.returncode or len(pages) != 2:
            raise ValueError('Both cloud PDF page previews required')
        if sha(pdf) != prior_pdfs.get(date):
            raise ValueError('Factsheet bytes differ from the previously verified baseline')
        if sha(pptx) != row['presentation_sha256'] or sha(pdf) != row['factsheet_sha256']:
            raise ValueError('Downloaded artifact changed during visual export')
        receipt = {'period_end': date, 'presentation_sha256': sha(pptx), 'factsheet_sha256': sha(pdf),
                   'matched_slides': matched, 'different_slides': different, 'pdf_matches_verified_baseline': True,
                   'pdf_previews': [{'path': str(p), 'sha256': sha(p)} for p in pages]}
        save(output / 'comparison.json', receipt)
        return receipt

    items, errors = [], []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(check, row): row['period_end'] for row in proof['proof']['artifacts']}
        for future in as_completed(futures):
            try:
                row = future.result()
                items.append(row)
                print(json.dumps({'period': row['period_end'], 'reviewed_pixel_identical_slides': len(row['matched_slides']),
                                  'different_slides': len(row['different_slides']), 'pdf_baseline_unchanged': True}), flush=True)
            except Exception as error:
                errors.append({'period_end': futures[future], 'error_class': type(error).__name__,
                               'error': str(error) if isinstance(error, ValueError) else 'Details redacted'})
    matched = sum(len(r['matched_slides']) for r in items)
    different = sum(len(r['different_slides']) for r in items)
    if sha(proof_path) != proof_sha or sha(script_path) != script_sha:
        raise ValueError('Qualification or inspection code changed')
    record = {'status': 'reviewed_slide_pixel_identity_passed' if matched == 420 and not errors else 'manual_review_required',
              'image': proof['image'], 'runtime_qualified_sha256': proof_sha,
              'accepted_visual_receipt_sha256': reference_sha, 'preview_exporter_sha256': script_sha,
              'reviewed_reference_root': str(preview_root),
              'prior_factsheet_proof_sha256': sha(prior_path), 'months_rendered': len(items),
              'matched_reviewed_slides': matched, 'different_slides': different,
              'factsheets_unchanged_from_verified_baseline': len(items), 'pdf_pages_rendered': 2*len(items),
              'pdf_new_full_page_inspection': 'pending', 'items': sorted(items, key=lambda r: r['period_end']), 'errors': errors,
              'native_microsoft_powerpoint_tested': False, 'production_promotion': False,
              'source_data_changed': False, 'cloud_writes': 0}
    save(stage / 'receipt.json', record)
    print(json.dumps({k: record[k] for k in ('status','months_rendered','matched_reviewed_slides','different_slides','pdf_pages_rendered','errors')}))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
