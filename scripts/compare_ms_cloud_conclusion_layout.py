"""Explain slide 17 differences without altering or visually approving outputs."""
import argparse
import json
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import build_ms_presentation_worker_candidate as build

NS = '{http://schemas.openxmlformats.org/drawingml/2006/main}'


def compare(cloud, accepted):
    with ZipFile(cloud) as current, ZipFile(accepted) as previous:
        left = ET.fromstring(previous.read('ppt/slides/slide17.xml'))
        right = ET.fromstring(current.read('ppt/slides/slide17.xml'))
    a, b = list(left.iter(NS + 'normAutofit')), list(right.iter(NS + 'normAutofit'))
    if len(a) != len(b):
        raise ValueError('Autofit element count changed')
    changes = []
    for index, (old, new) in enumerate(zip(a, b)):
        if old.attrib == new.attrib:
            continue
        if (old.get('fontScale') not in ('88662', '91137', '92537', '93537', '95875', '96087', '96875', '98621', '98796')
                or new.get('fontScale') != '100000'
                or {k:v for k,v in old.attrib.items() if k != 'fontScale'}
                != {k:v for k,v in new.attrib.items() if k != 'fontScale'}):
            raise ValueError('Unexplained autofit difference')
        changes.append({'element': index, 'accepted_font_scale': old.get('fontScale'),
                        'cloud_font_scale': new.get('fontScale')})
        old.set('fontScale', new.get('fontScale'))
    if not changes or ET.tostring(left) != ET.tostring(right):
        raise ValueError('Conclusion text, geometry or other styling differs')
    return changes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True)
    runtime = parser.parse_args().folder.resolve() / 'runtime-v1'
    path = runtime / 'cloud-visual-v1/receipt.json'
    visual = json.loads(path.read_bytes())
    if (visual['months_rendered'] != 21 or visual['matched_reviewed_slides'] != 399
            or visual['different_slides'] != 21 or visual['errors']):
        raise ValueError('Exact all-month manual-review scope required')
    items = []
    for row in visual['items']:
        if [x['slide'] for x in row['different_slides']] != [17]:
            raise ValueError('Only conclusions slide 17 may differ')
        date = row['period_end']
        cloud = runtime / 'candidate-outputs' / f'AlphaWorld_MS_Presentation_{date}.pptx'
        accepted = build.ROOT / '.work/alpha-ms-presentation-all-months-20261007-r4/output' / f'AlphaWorld_MS_Presentation_{date}_HKD_Review.pptx'
        prior = json.loads((accepted.parent.parent / 'receipts' / (date + '.json')).read_bytes())
        if build.sha(cloud) != row['presentation_sha256'] or build.sha(accepted) != prior['output_sha256']:
            raise ValueError('Output identity changed')
        changes = compare(cloud, accepted)
        items.append({'period_end': date, 'slide': 17, 'cloud_sha256': build.sha(cloud),
                      'accepted_sha256': build.sha(accepted), 'only_autofit_scale_differs': True,
                      'text_and_geometry_unchanged': True, 'changes': changes})
    record = {'status': 'bounded_autofit_difference_verified', 'image': visual['image'],
              'cloud_visual_receipt_sha256': build.sha(path), 'slides_explained': 21,
              'visual_approval': False, 'items': items}
    with (runtime / 'conclusion-layout-comparison.json').open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2, allow_nan=False)
    print(json.dumps({'status': record['status'], 'slides_explained': 21, 'visual_approval': False}))


if __name__ == '__main__':
    main()
