"""Carry an existing visual review only through exact report and PNG identity.

Does not claim a new human/model inspection or approve reports for release.
"""
import argparse
import hashlib
import json
from pathlib import Path
import build_ms_presentation_worker_candidate as build

PRIOR = build.ROOT / '.work/alpha-ms-presentation-worker-candidate-20261007-r5/runtime-v1'
INSPECTION_CANONICAL_SHA = 'a4058bacc7a4a3274a43730fdd763d10e5d6308b52e77952ed954fc7fd8a8054'
ACCEPTANCE_CANONICAL_SHA = '9bc090fb2645b90a3bed8b359a4d138a94e5ef5cdee7b2cc7a8a689be10a5491'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def evidence(runtime):
    require(runtime.parent.name.startswith('alpha-ms-format-worker-candidate-'), 'Format-only review inheritance required')
    acceptance = json.loads((PRIOR / 'candidate-render-acceptance.json').read_bytes())
    inspection_path = PRIOR / 'pdf-page-inspection.json'
    inspection = json.loads(inspection_path.read_bytes())
    require(canonical_sha({k:v for k,v in acceptance.items() if k != 'protected_receipt'}) == ACCEPTANCE_CANONICAL_SHA
            and acceptance['protected_receipt']['sha256'] == ACCEPTANCE_CANONICAL_SHA
            and acceptance['protected_receipt']['protected_readback_verified'] is True,
            'Exact protected prior output acceptance required')
    require(canonical_sha(inspection) == INSPECTION_CANONICAL_SHA
            and inspection['status'] == 'passed' and inspection['pages_inspected'] == 42
            and acceptance['protected_evidence_receipts']['pdf_page_inspection']['sha256'] == INSPECTION_CANONICAL_SHA,
            'Exact protected prior 42-page inspection required')
    prior_proof = json.loads((PRIOR / 'candidate-qualified.json').read_bytes())
    require(build.sha(PRIOR / 'candidate-qualified.json') == acceptance['candidate_qualified_sha256'],
            'Prior output proof identity changed')
    old_reports = {r['period_end']:r['factsheet_sha256'] for r in prior_proof['proof']['artifacts']}
    old_pages = {(r['period_end'],Path(r['path']).name):r for r in inspection['items']}
    visual_path = runtime / 'cloud-visual-v1/receipt.json'
    visual = json.loads(visual_path.read_bytes())
    require(visual['months_rendered'] == 21 and not visual['errors'] and len(old_pages) == 42,
            'Complete rendered page coverage required')
    items = []
    for row in visual['items']:
        date = row['period_end']
        require(row['factsheet_sha256'] == old_reports[date]
                == build.sha(runtime / 'candidate-outputs' / f'AlphaWorld_MS_Factsheet_{date}.pdf'),
                'Report bytes changed since prior inspection')
        for page in row['pdf_previews']:
            old = old_pages[(date,Path(page['path']).name)]
            require(page['sha256'] == old['sha256'] == build.sha(Path(page['path'])) == build.sha(Path(old['path']))
                    and old['status'] == 'passed', 'Rendered PDF page differs from inspected reference')
            items.append({**page, 'period_end':date, 'status':'passed', 'prior_inspected_path':old['path']})
    require(len(items) == 42 and len({r['path'] for r in items}) == 42,
            'Unique full page identity coverage required')
    return {'status':'passed', 'review_basis':'exact_report_and_rendered_page_identity_to_protected_prior_inspection',
            'cloud_visual_receipt_sha256':build.sha(visual_path), 'pages_inspected':0,
            'pages_verified_by_identity':42, 'items':items,
            'prior_inspection_sha256':build.sha(inspection_path),
            'prior_inspection_canonical_sha256':INSPECTION_CANONICAL_SHA,
            'prior_output_acceptance_canonical_sha256':ACCEPTANCE_CANONICAL_SHA,
            'production_promotion_authorized':False, 'new_visual_inspection_claimed':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    runtime = parser.parse_args().folder.resolve() / 'runtime-v1'
    record = evidence(runtime)
    with (runtime / 'pdf-page-inspection.json').open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2, allow_nan=False)
    print(json.dumps({'pages_newly_inspected':0, 'pages_verified_by_exact_identity':42, 'status':'passed'}))


if __name__ == '__main__':
    main()
