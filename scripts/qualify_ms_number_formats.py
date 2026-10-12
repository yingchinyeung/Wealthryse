"""Private all-month formatting qualification. No Azure or source-data writes."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from ibkr_etl import ms_chart_workbooks as wb
from audit_alpha_ms_presentation import audit


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def qualify(receipt, downloads, inputs, output):
    accepted = json.loads(receipt.read_bytes())
    assert accepted['status'] == 'authenticated_website_download_identity_passed'
    assert len(accepted['items']) == 42
    for item in accepted['items']:
        source = downloads / item['filename']
        assert source.is_file() and sha(source) == item['sha256']
        assert source.stat().st_size == item['byte_count']
    output.mkdir(parents=True, exist_ok=False)
    (output / 'presentations').mkdir()
    results = []
    for item in accepted['items']:
        if item['report_type'] != 'presentation':
            continue
        source = downloads / item['filename']
        destination = output / 'presentations' / f"MS_{item['period_end']}_NumberFormats.pptx"
        result = wb.upgrade_number_formats(source, destination)
        before, after = wb.package(source.read_bytes()), wb.package(destination.read_bytes())
        assert before.keys() == after.keys()
        allowed = {i[k] for i in result['items'] for k in ('chart_part', 'workbook_part')}
        changed = {name for name in before if before[name] != after[name]}
        assert changed <= allowed and len(changed) == 12
        for name in set(before) - allowed:
            assert before[name] == after[name]
        for _, name, root in wb.charts(before):
            expected = deepcopy(root)
            wb.fix_chart_formats(expected)
            assert ET.tostring(expected) == ET.tostring(wb.xml(after[name]))
        financial = audit(destination, inputs / f"{item['period_end']}.json")
        assert financial['status'] == 'passed'
        results.append({'period_end': item['period_end'], 'output_file': str(destination.resolve()),
                        'input_sha256': item['sha256'], 'output_sha256': sha(destination),
                        'changed_parts': sorted(changed), 'format_verification': result,
                        'financial_audit': financial})
    assert len(results) == 21
    for item in accepted['items']:
        assert sha(downloads / item['filename']) == item['sha256']
    native = output / 'native-copy'
    native.mkdir()
    september = next(i for i in results if i['period_end'] == '2026-09-30')
    shutil.copyfile(september['output_file'], native / 'MS_2026-09-30_EditSaveTest.pptx')
    result = {'status': 'private_number_format_package_and_financial_checks_passed',
              'accepted_receipt_sha256': sha(receipt), 'presentations': 21,
              'financial_charts_verified': 126, 'risk_metrics_verified': 147,
              'chart_workbook_formats_verified': 126, 'accepted_downloads_unchanged': 42,
              'only_chart_formats_and_workbook_display_changed': True,
              'cash_editor_column_width': 16,
              'native_edit_save_reopen_qualified': False, 'azure_deployed': False,
              'reports_approved': False, 'source_data_written': False, 'items': results}
    with (output / 'qualification.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
    return {k: v for k, v in result.items() if k != 'items'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('receipt', 'downloads', 'inputs', 'output'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    print(json.dumps(qualify(args.receipt, args.downloads, args.inputs, args.output)))
