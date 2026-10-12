"""Compare cloud editable-chart bindings with the accepted private decks."""
import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from ibkr_etl.ms_chart_workbooks import verify_ms_chart_workbooks

ROOT = Path(__file__).resolve().parents[1]
NS = {'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      'p': 'http://schemas.openxmlformats.org/package/2006/relationships'}


def bindings(path):
    rows = []
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        charts = sorted(name for name in names if name.startswith('ppt/slides/charts/chart')
                        and name.endswith('.xml') and '/_rels/' not in name)
        for name in charts:
            external = ET.fromstring(archive.read(name)).find('c:chart/c:externalData', NS)
            # externalData is a direct chartSpace child in the exported OOXML.
            if external is None:
                external = ET.fromstring(archive.read(name)).find('c:externalData', NS)
            rel_name = posixpath.join(posixpath.dirname(name), '_rels', posixpath.basename(name) + '.rels')
            target = None
            if external is not None and rel_name in names:
                rid = external.get('{' + NS['r'] + '}id')
                relationships = ET.fromstring(archive.read(rel_name)).findall('p:Relationship', NS)
                matches = [r for r in relationships if r.get('Id') == rid
                           and r.get('Type') == NS['r'] + '/package' and r.get('TargetMode') != 'External']
                if len(matches) == 1:
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(name), matches[0].get('Target', '')))
            valid = bool(target and target.startswith('ppt/embeddings/') and target.endswith('.xlsx') and target in names)
            rows.append({'chart': name, 'embedded_workbook_bound': valid,
                'workbook': target if valid else None,
                'workbook_sha256': hashlib.sha256(archive.read(target)).hexdigest() if valid else None})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    folder = args.folder.resolve()
    runtime = folder / 'runtime-v1'
    require_format = folder.name.startswith('alpha-ms-format-worker-candidate-')
    proof = json.loads((runtime / 'candidate-qualified.json').read_bytes())
    rows = []
    for item in proof['proof']['artifacts']:
        period = item['period_end']
        cloud = runtime / 'candidate-outputs' / ('AlphaWorld_MS_Presentation_' + period + '.pptx')
        accepted = ROOT / '.work/alpha-ms-presentation-all-months-20261007-r4/output' / ('AlphaWorld_MS_Presentation_' + period + '_HKD_Review.pptx')
        if hashlib.sha256(cloud.read_bytes()).hexdigest() != item['presentation_sha256']:
            raise ValueError('Cloud artifact identity differs')
        prior = json.loads((ROOT / '.work/alpha-ms-presentation-all-months-20261007-r4/receipts' / (period + '.json')).read_bytes())
        if hashlib.sha256(accepted.read_bytes()).hexdigest() != prior['output_sha256']:
            raise ValueError('Accepted private artifact identity differs')
        actual, expected = bindings(cloud), bindings(accepted)
        if len(actual) != 6 or len(expected) != 6 or not all(r['embedded_workbook_bound'] for r in expected):
            raise ValueError('Six known approved chart/workbook bindings required')
        cell_validation = verify_ms_chart_workbooks(cloud, require_format=require_format) if all(r['embedded_workbook_bound'] for r in actual) else None
        expected_cell_validation = verify_ms_chart_workbooks(accepted)
        rows.append({'period_end': period, 'cloud_sha256': item['presentation_sha256'],
            'accepted_sha256': prior['output_sha256'], 'cloud': actual, 'accepted': expected,
            'cloud_cell_validation': cell_validation, 'accepted_cell_validation': expected_cell_validation,
            'missing_cloud_workbooks': sum(not r['embedded_workbook_bound'] for r in actual)})
    missing = sum(r['missing_cloud_workbooks'] for r in rows)
    record = {'status': 'blocked' if missing else 'passed', 'image': proof['image'],
        'months_checked': len(rows), 'missing_cloud_chart_workbooks': missing, 'items': rows,
        'embedded_workbooks_and_cells_verified': sum(
            r['cloud_cell_validation']['embedded_workbooks_verified'] if r['cloud_cell_validation'] else 0 for r in rows),
        'verifier_sha256': hashlib.sha256((ROOT / 'src/ibkr_etl/ms_chart_workbooks.py').read_bytes()).hexdigest(),
        'financial_values_previously_verified': True, 'source_data_changed': False,
        'report_release_authorized': False, 'cloud_writes': 0,
        'explicit_number_formats_and_hkd_editor_width_required': require_format,
        'reason': 'Cloud decks must retain the embedded editable chart-data bindings of the accepted private version'}
    with (runtime / 'chart-workbook-acceptance.json').open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2, allow_nan=False)
    print(json.dumps({k: record[k] for k in ('status', 'months_checked', 'missing_cloud_chart_workbooks', 'cloud_writes')}))


if __name__ == '__main__':
    main()
