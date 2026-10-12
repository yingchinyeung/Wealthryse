"""Record the completed model inspection, not perform or infer visual approval.

All listed page PNGs and conclusion-slide PNGs were displayed individually in
the 8 October continuation. Run only to bind those observations to exact bytes.
No renderer changes, report release or production promotion are authorized.
"""
import json
from pathlib import Path

import build_ms_presentation_worker_candidate as build

REVIEWED = ('2025-01-31', '2025-02-28', '2025-03-31', '2025-04-30', '2025-05-31',
            '2025-06-30', '2025-07-31', '2025-08-31', '2025-09-30', '2025-10-31',
            '2025-11-30', '2025-12-31', '2026-01-31', '2026-02-28', '2026-03-31',
            '2026-04-30', '2026-05-31', '2026-06-30', '2026-07-31', '2026-08-31', '2026-09-30')


def main():
    runtime = build.ROOT / '.work/alpha-ms-presentation-worker-candidate-20261007-r5/runtime-v1'
    visual_path = runtime / 'cloud-visual-v1/receipt.json'
    visual = json.loads(visual_path.read_bytes())
    if (tuple(r['period_end'] for r in visual['items']) != REVIEWED or visual['errors']
            or visual['matched_reviewed_slides'] != 399 or visual['different_slides'] != 21):
        raise ValueError('Inspection scope differs from displayed images')
    pages, slides = [], []
    for row in visual['items']:
        if [s['slide'] for s in row['different_slides']] != [17]:
            raise ValueError('Unexpected manual slide scope')
        for page in row['pdf_previews']:
            if build.sha(Path(page['path'])) != page['sha256']:
                raise ValueError('Displayed page identity changed')
            pages.append({**page, 'period_end': row['period_end'], 'status': 'passed'})
        path = runtime / 'cloud-visual-v1' / row['period_end'] / 'slides/slide-17.png'
        digest = row['different_slides'][0]['preview_sha256']
        if build.sha(path) != digest:
            raise ValueError('Displayed slide identity changed')
        slides.append({'period_end': row['period_end'], 'slide': 17,
                       'path': str(path), 'sha256': digest, 'status': 'passed'})
    common = {'status': 'passed', 'review_date': '2026-10-08',
              'reviewer': 'Codex model visual inspection of individually displayed images',
              'cloud_visual_receipt_sha256': build.sha(visual_path),
              'production_promotion_authorized': False}
    records = {
        'pdf-page-inspection.json': {**common, 'pages_inspected': 42, 'items': pages,
            'criteria': 'Full-page review: identity/date, HKD/HIBOR headings, chart axes/legends, 25-point histogram ticks where in range, equal allocation boxes, disclaimer spacing and footer bounds. No clipping or overlap found.'},
        'cloud-slide-inspection.json': {**common, 'slides_inspected': 21, 'items': slides,
            'layout_comparison_sha256': build.sha(runtime / 'conclusion-layout-comparison.json'),
            'criteria': 'Full-slide review: all conclusions bullets and the bottom summary remain readable and within their boxes; branding and three-column geometry preserved. Independent comparison confirms only autofit font scaling differs. All content remains visible; no overlaps or clipping found.'}}
    for name, value in records.items():
        with (runtime / name).open('x', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
    print(json.dumps({'pdf_pages_inspected': len(pages), 'conclusion_slides_inspected': len(slides),
                      'production_promotion_authorized': False}))


if __name__ == '__main__':
    main()
