"""Retain completed render/audit evidence, without authorizing deployment or release.

Read-only database and isolated executions are qualification only. Recovery,
live-switch rollback and owner report approvals are separate outstanding gates.
"""
import argparse
import json
from pathlib import Path

import build_ms_presentation_worker_candidate as build
import compare_ms_cloud_conclusion_layout as conclusion
from qualify_ms_presentation_worker_runtime import candidate_gate
from run_specialist_gateway_read_verification import retain


def read(path):
    return json.loads(path.read_bytes())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_evidence(runtime, image):
    qualified_path = runtime / 'candidate-qualified.json'
    qualified = read(qualified_path)
    require(qualified['status'] == 'passed' and qualified['image'] == image
            and qualified['proof']['count'] == 21 and qualified['artifact_readback_verified']
            and qualified['persistent_jobs_unchanged'] and qualified['protected_receipt']['protected_readback_verified'],
            'Complete protected runtime/readback qualification required')
    audit = read(runtime / 'cloud-local-audit-v1/receipt.json')
    workbooks = read(runtime / 'chart-workbook-acceptance.json')
    visual = read(runtime / 'cloud-visual-v1/receipt.json')
    require(audit['status'] == 'passed' and audit['image'] == image and audit['months_verified'] == 21
            and not audit['errors'] and audit['runtime_qualified_sha256'] == build.sha(qualified_path)
            and all(row['embedded_workbooks_verified'] == 6 for row in audit['items']),
            'Bound independent all-month workbook/financial/package audits required')
    require(workbooks['status'] == 'passed' and workbooks['image'] == image and workbooks['months_checked'] == 21
            and workbooks['missing_cloud_chart_workbooks'] == 0
            and workbooks['embedded_workbooks_and_cells_verified'] == 126,
            'All 126 workbooks and cells must pass')
    manual = visual['status'] == 'manual_review_required'
    require(visual['status'] in ('reviewed_slide_pixel_identity_passed', 'manual_review_required') and visual['image'] == image
            and visual['runtime_qualified_sha256'] == build.sha(qualified_path)
            and (visual['matched_reviewed_slides'], visual['different_slides']) == ((399,21) if manual else (420,0))
            and visual['factsheets_unchanged_from_verified_baseline'] == 21 and visual['pdf_pages_rendered'] == 42
            and not visual['errors'], 'Complete cloud/reference rendered visual comparison required')
    extra = {}
    if manual:
        review = read(runtime / 'cloud-slide-inspection.json')
        explanation_path = runtime / 'conclusion-layout-comparison.json'
        explanation = read(explanation_path)
        require(review['status'] == 'passed' and review['slides_inspected'] == 21
                and review['cloud_visual_receipt_sha256'] == build.sha(runtime / 'cloud-visual-v1/receipt.json')
                and review['layout_comparison_sha256'] == build.sha(explanation_path)
                and explanation['status'] == 'bounded_autofit_difference_verified'
                and explanation['image'] == image and explanation['slides_explained'] == 21
                and explanation['cloud_visual_receipt_sha256'] == review['cloud_visual_receipt_sha256'],
                'Bound explicit conclusion-slide inspection required')
        expected_slides = {(r['period_end'], s['slide']): s['preview_sha256']
                           for r in visual['items'] for s in r.get('different_slides', [])}
        inspected = {(r['period_end'], r['slide']): r['sha256'] for r in review['items'] if r['status'] == 'passed'}
        require(len(review['items']) == len(inspected) == 21 and inspected == expected_slides
                and {slide for _, slide in expected_slides} == {17},
                'Complete unique manual slide review required')
        for date, slide in expected_slides:
            cloud = runtime / 'candidate-outputs' / f'AlphaWorld_MS_Presentation_{date}.pptx'
            accepted = build.ROOT / '.work/alpha-ms-presentation-all-months-20261007-r4/output' / f'AlphaWorld_MS_Presentation_{date}_HKD_Review.pptx'
            conclusion.compare(cloud, accepted)
            preview = runtime / 'cloud-visual-v1' / date / 'slides' / f'slide-{slide:02d}.png'
            require(build.sha(preview) == expected_slides[(date,slide)], 'Manually inspected slide preview changed')
        extra = {'conclusion_difference': explanation, 'cloud_slide_inspection': review}
    pages = read(runtime / 'pdf-page-inspection.json')
    inherited = pages.get('review_basis') == 'exact_report_and_rendered_page_identity_to_protected_prior_inspection'
    if inherited:
        from carry_ms_unchanged_pdf_review import evidence as inherited_evidence
        require(pages == inherited_evidence(runtime), 'Inherited PDF review identity changed')
    require(pages['status'] == 'passed' and pages['cloud_visual_receipt_sha256'] == build.sha(runtime / 'cloud-visual-v1/receipt.json')
            and ((pages['pages_inspected'] == 0 and pages.get('pages_verified_by_identity') == 42) if inherited else pages['pages_inspected'] == 42)
            and len(pages['items']) == 42,
            'Complete explicitly reviewed PDF pages required')
    expected = {page['path']: page['sha256'] for row in visual['items'] for page in row['pdf_previews']}
    actual = {row['path']: row['sha256'] for row in pages['items']}
    require(actual == expected and all(build.sha(Path(path)) == digest for path, digest in expected.items()),
            'Inspected PDF preview identities changed')
    periods = {row['period_end'] for row in qualified['proof']['artifacts']}
    require(len(periods) == 21 and {row['period_end'] for row in visual['items']} == periods,
            'Unique all-month visual/runtime coverage required')
    for row in qualified['proof']['artifacts']:
        date = row['period_end']
        for kind, stem, suffix in (('factsheet', 'Factsheet', 'pdf'), ('presentation', 'Presentation', 'pptx')):
            path = runtime / 'candidate-outputs' / f'AlphaWorld_MS_{stem}_{date}.{suffix}'
            require(build.sha(path) == row[kind + '_sha256'], 'Qualified downloaded artifact changed')
    for row in visual['items']:
        slides = row['matched_slides'] + row.get('different_slides', [])
        require(len(slides) == 20 and {s['slide'] for s in slides} == set(range(1,21)),
                'Complete unique slide identity evidence required')
        for slide in row['matched_slides']:
            path = runtime / 'cloud-visual-v1' / row['period_end'] / 'slides' / f"slide-{slide['slide']:02d}.png"
            require(build.sha(path) == slide['preview_sha256'] == slide['reviewed_reference_sha256'],
                    'Compared cloud slide preview changed')
    return qualified, {'independent_audit': audit, 'workbook_audit': workbooks, 'cloud_visual': visual,
                       'pdf_page_inspection': pages, **extra}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    folder = args.folder.resolve()
    source, image = candidate_gate(folder)
    runtime = folder / 'runtime-v1'
    if (runtime / 'candidate-render-acceptance.json').exists():
        raise ValueError('Preserve existing acceptance evidence')
    qualified, evidence = validate_evidence(runtime, image)
    protected = {name: retain(value, folder.name + '-' + name) for name, value in evidence.items()}
    record = {'status': 'isolated_cloud_render_and_output_acceptance_passed', 'image': image,
        'execution': qualified['execution'], 'months_verified': 21, 'factsheets_verified': 21,
        'presentations_verified': 21, 'financial_charts_verified': 126, 'risk_metrics_verified': 147,
        'embedded_workbooks_verified': 126,
        'reviewed_pixel_identical_slides': evidence['cloud_visual']['matched_reviewed_slides'],
        'individually_inspected_cloud_slides': evidence.get('cloud_slide_inspection', {}).get('slides_inspected', 0),
        'total_slides_visually_covered': 420,
        'pdf_pages_inspected': evidence['pdf_page_inspection']['pages_inspected'],
        'pdf_pages_verified_by_identity': evidence['pdf_page_inspection'].get('pages_verified_by_identity', 0),
        'source_intent_sha256': build.sha(folder / 'source-intent.json'),
        'candidate_qualified_sha256': build.sha(runtime / 'candidate-qualified.json'),
        'protected_evidence_receipts': protected, 'persistent_reporting_jobs_unchanged': True,
        'database_writes': 0, 'ibkr_source_data_changed': False, 'reports_released': False,
        'onboarding_activated': False, 'website_batch_registered': False,
        'native_microsoft_powerpoint_verified': False,
        'full_live_image_switch_rollback_exercised': False, 'production_promotion_authorized': False,
        'next_gate': 'Recovery/live-switch rollback safeguards before scoped reporting-worker deployment'}
    receipt = retain(record, folder.name + '-candidate-render-acceptance')
    build.save(runtime, 'candidate-render-acceptance', {**record, 'protected_receipt': receipt})
    print(json.dumps({k: record[k] for k in ('status','execution','months_verified','embedded_workbooks_verified','reviewed_pixel_identical_slides','production_promotion_authorized')}))


if __name__ == '__main__':
    main()
