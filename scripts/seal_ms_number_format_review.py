"""Bind reviewed private MS previews and explicit native observations, not a release gate."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from ibkr_etl import ms_chart_workbooks as wb


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_bytes())


def seal(previous, current, reference, accepted_receipt, downloads, destination):
    first, last = load(previous / 'qualification.json'), load(current / 'qualification.json')
    comparison = load(current / 'all-month-previews/comparison.json')
    accepted = load(accepted_receipt)
    baseline = load(reference / 'receipt.json')
    if baseline['status'] != 'accepted_pixel_identity_passed' or baseline['differences'] != 0:
        raise ValueError('Previously accepted visual reference required')
    if comparison['candidate_proof_sha256'] != sha(current / 'qualification.json'):
        raise ValueError('Preview/candidate proof binding changed')
    periods = [i['period_end'] for i in last['items']]
    expected = ['2025-01-31', '2025-02-28', '2025-03-31', '2025-04-30',
                '2025-05-31', '2025-06-30', '2025-07-31', '2025-08-31',
                '2025-09-30', '2025-10-31', '2025-11-30', '2025-12-31',
                '2026-01-31', '2026-02-28', '2026-03-31', '2026-04-30',
                '2026-05-31', '2026-06-30', '2026-07-31', '2026-08-31', '2026-09-30']
    if sorted(periods) != expected or sorted(i['period_end'] for i in first['items']) != expected:
        raise ValueError('Exact 21-month coverage required')
    if accepted['status'] != 'authenticated_website_download_identity_passed' or len(accepted['items']) != 42:
        raise ValueError('Exact accepted website file inventory required')
    for item in accepted['items']:
        file = downloads / item['filename']
        if sha(file) != item['sha256'] or file.stat().st_size != item['byte_count']:
            raise ValueError('Accepted website download changed')
    earlier = {i['period_end']: i for i in first['items']}
    accepted_visual = {i['period_end']: i for i in baseline['items']}
    previews = {i['period_end']: i for i in comparison['items']}
    if sorted(previews) != expected:
        raise ValueError('Exact preview inventory required')
    items = []
    for item in last['items']:
        period = item['period_end']
        old_path, new_path = Path(earlier[period]['output_file']), Path(item['output_file'])
        if sha(old_path) != earlier[period]['output_sha256'] or sha(new_path) != item['output_sha256']:
            raise ValueError('Private presentation changed')
        if item['financial_audit']['status'] != 'passed':
            raise ValueError('Financial audit not passed')
        before, after = wb.package(old_path.read_bytes()), wb.package(new_path.read_bytes())
        wb.verify_bytes(new_path.read_bytes(), require_format=True)
        changed = {name for name in before if before[name] != after[name]}
        cash = next(i['workbook_part'] for i in item['format_verification']['items'] if i['owner_slide'] == 11)
        if before.keys() != after.keys() or changed != {cash}:
            raise ValueError('Column fix changed more than the cash workbook')
        a, b = wb.package(before[cash], workbook=True), wb.package(after[cash], workbook=True)
        if a.keys() != b.keys() or {k for k in a if a[k] != b[k]} != {'xl/worksheets/sheet1.xml'}:
            raise ValueError('Column fix changed other workbook parts')
        sheet = deepcopy(wb.xml(b['xl/worksheets/sheet1.xml']))
        sheet.remove(sheet.find('x:cols', wb.NS))
        if ET.tostring(sheet) != ET.tostring(wb.xml(a['xl/worksheets/sheet1.xml'])):
            raise ValueError('Column fix changed worksheet data')
        preview = previews[period]
        if preview['changed_slides'] != [6, 9, 10, 11] or len(preview['previews']) != 20:
            raise ValueError('Unexpected visual delta set')
        baseline_hashes = {p['name']: p['new_sha256'] for p in accepted_visual[period]['previews'] if p['kind'] == 'slides'}
        for p in preview['previews']:
            name = f"slide-{p['slide']:02d}.png"
            old, new = reference / period / 'slides' / name, current / 'all-month-previews' / period / name
            if sha(old) != p['prior_sha256'] or sha(old) != baseline_hashes[name] or sha(new) != p['new_sha256']:
                raise ValueError('Reviewed preview binding changed')
            if sha(previous / 'all-month-previews' / period / name) != p['new_sha256']:
                raise ValueError('Cash editor width changed visible slides')
        items.append({'period_end': period, 'presentation_sha256': item['output_sha256'],
                      'manually_reviewed_changed_slides': [6, 9, 10, 11],
                      'unchanged_previews_carried_forward': 16, 'cash_width_only_delta_from_v1': True})
    native = load(current / 'native-ui-observations.json')
    saved = current / 'native-copy/MS_2026-09-30_EditSaveTest.pptx'
    check = load(current / 'native-saved-check.json')
    if native['v2_saved_copy_sha256'] != sha(saved) or check['saved_sha256'] != sha(saved):
        raise ValueError('Native saved copy changed')
    if check['status'] != 'office_saved_chart_values_and_number_formats_passed' or check['charts_verified'] != 6:
        raise ValueError('Native saved format/value checks incomplete')
    result = {'status': 'private_all_month_format_financial_and_visual_delta_review_passed',
              'months': 21, 'slides_rendered': 420, 'changed_slides_manually_reviewed_full_size': 84,
              'unchanged_slides_hash_matched_to_accepted_reference': 336,
              'financial_charts_verified': 126, 'risk_metrics_verified': 147,
              'accepted_downloads_rehashed_unchanged': 42,
              'cash_column_fix_preview_identity_to_v1': 420,
              'representative_chart_family_editor_checks': 6,
              'all_month_native_open_complete': False, 'azure_release_qualified': False,
              'azure_changed': False, 'reports_approved': False, 'source_data_written': False,
              'candidate_proof_sha256': sha(current / 'qualification.json'),
              'preview_comparison_sha256': sha(current / 'all-month-previews/comparison.json'),
              'accepted_visual_receipt_sha256': sha(reference / 'receipt.json'),
              'native_observations_sha256': sha(current / 'native-ui-observations.json'),
              'native_saved_receipt_sha256': sha(current / 'native-saved-check.json'),
              'items': sorted(items, key=lambda i: i['period_end'])}
    with destination.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
    return {k: v for k, v in result.items() if k != 'items'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('previous', 'current', 'reference', 'accepted_receipt', 'downloads', 'destination'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    print(json.dumps(seal(args.previous, args.current, args.reference, args.accepted_receipt, args.downloads, args.destination)))
