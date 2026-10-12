"""Read-only independent audit of qualified cloud files, not visual acceptance."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILL = Path('C:/Users/antho/.codex/plugins/cache/openai-primary-runtime/presentations/26.1007.11041/skills/presentations')
DEFAULT_PYTHON = Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe')
BASELINE_WARNINGS = {('object_outside_slide_boundary', slide, identity)
    for slide, identity in ((1, '125'), (1, '2'), (1, '121'), (2, '37'), (20, '5'), (20, '7'), (20, '11'))}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    candidate = args.folder.resolve()
    runtime = candidate / 'runtime-v1'
    qualified_path = runtime / 'candidate-qualified.json'
    qualified_sha256 = sha(qualified_path)
    qualified = json.loads(qualified_path.read_bytes())
    if qualified['status'] != 'passed' or qualified['proof']['count'] != 21:
        raise ValueError('Complete qualified 21-month cloud runtime proof required')
    isolated = candidate / 'local-runtime'
    release = json.loads((candidate / 'context/MS_PRESENTATION_WORKER_RELEASE.json').read_bytes())
    auditors = ('src/ibkr_etl/alpha_world_ms_presentation.py', 'src/ibkr_etl/presentation_design.py',
                'scripts/audit_alpha_ms_presentation.py', 'scripts/validate_monthly_presentation.py',
                'scripts/validate_subject_presentation.py')
    if release.get('chart_workbooks_required'):
        auditors += ('src/ibkr_etl/ms_chart_workbooks.py',)
    for name in auditors:
        if sha(isolated / name) != release['installed_files_sha256'][name]:
            raise ValueError('Isolated candidate auditor changed')
    output = runtime / 'cloud-local-audit-v1'
    output.mkdir(exist_ok=False)
    validators = {name: sha(DEFAULT_SKILL / 'container_tools' / name)
                  for name in ('inspect_presentation_package_integrity.py',
                               'inspect_presentation_layout_geometry.py')}
    environment = {**os.environ, 'PYTHONPATH': 'src;scripts', 'PYTHONUTF8': '1'}

    def check(row):
        period = row['period_end']
        pptx = runtime / 'candidate-outputs' / ('AlphaWorld_MS_Presentation_' + period + '.pptx')
        pdf = runtime / 'candidate-outputs' / ('AlphaWorld_MS_Factsheet_' + period + '.pdf')
        snapshot = ROOT / '.work/alpha-ms-presentation-hkd-20261007/inputs' / (period + '.json')
        if sha(pptx) != row['presentation_sha256'] or sha(pdf) != row['factsheet_sha256']:
            raise ValueError('Cloud file bytes changed: ' + period)
        input_sha256 = sha(snapshot)
        commands = {
            'financial': [str((ROOT / '.work/venv-test/Scripts/python.exe').resolve()),
                str(isolated / 'scripts/audit_alpha_ms_presentation.py'), str(pptx), str(snapshot)],
            'package': [str(DEFAULT_PYTHON), str(DEFAULT_SKILL / 'container_tools/inspect_presentation_package_integrity.py'), str(pptx), '--fail-on-findings'],
            'layout': [str(DEFAULT_PYTHON), str(DEFAULT_SKILL / 'container_tools/inspect_presentation_layout_geometry.py'), str(pptx),
                '--fail-on-findings', '--expected-slide-size-emu', '9144000,5143500',
                '--expected-slide-count', '20', '--validate-bullet-geometry', '--validate-heading-fit',
                *[option for number in (3, 5, 6, 9, 10, 11, 12, 13, 14, 15, 16)
                  for option in ('--require-native-table-slide', str(number))]],
        }
        if release.get('chart_workbooks_required'):
            commands['workbooks'] = [str((ROOT / '.work/venv-test/Scripts/python.exe').resolve()),
                                    '-m', 'ibkr_etl.ms_chart_workbooks', str(pptx)]
        checks = {}
        for name, command in commands.items():
            result = subprocess.run(command, cwd=isolated, env=environment,
                capture_output=True, text=True, encoding='utf-8', timeout=180)
            try:
                report = json.loads(result.stdout)
            except ValueError:
                raise ValueError('Invalid independent audit output: ' + period + '/' + name) from None
            write(output / (period + '-' + name + '.json'), {'returncode': result.returncode,
                'report': report, 'stderr': result.stderr})
            if result.returncode:
                raise ValueError('Independent audit failed: ' + period + '/' + name)
            checks[name] = report
        if release.get('chart_workbooks_required') and (
                checks['workbooks'].get('status') != 'passed'
                or checks['workbooks'].get('embedded_workbooks_verified') != 6
                or checks['workbooks'].get('owner_slides') != [6,7,8,9,10,11]
                or checks['workbooks'].get('output_sha256') != row['presentation_sha256']):
            raise ValueError('Independent workbook coverage incomplete: ' + period)
        if (checks['financial']['status'] != 'passed'
                or checks['financial']['financial_charts_verified'] != 6
                or checks['financial']['risk_metrics_verified'] != 7
                or checks['package']['slide_count'] != 20
                or checks['package']['finding_count'] != 0
                or checks['layout']['finding_count'] != 0):
            raise ValueError('Independent cloud coverage incomplete: ' + period)
        warnings = {(w['kind'], w.get('slide'), w.get('object_id'))
                    for w in checks['layout'].get('warnings', [])}
        if warnings - BASELINE_WARNINGS:
            raise ValueError('New cloud layout warning requires review: ' + period)
        if (sha(snapshot) != input_sha256 or checks['financial']['snapshot_sha256'] != input_sha256
                or sha(pptx) != row['presentation_sha256'] or sha(pdf) != row['factsheet_sha256']):
            raise ValueError('Cloud output or frozen input changed during independent audit: ' + period)
        return {'period_end': period, 'presentation_sha256': sha(pptx),
            'factsheet_sha256': sha(pdf), 'input_sha256': sha(snapshot),
            'financial_charts_verified': 6, 'risk_metrics_verified': 7, 'slides': 20,
            'embedded_workbooks_verified': checks.get('workbooks', {}).get('embedded_workbooks_verified'),
            'package_findings': 0, 'layout_findings': 0, 'baseline_warning_count': len(warnings)}

    results, errors = [], []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(check, row): row['period_end'] for row in qualified['proof']['artifacts']}
        for future in as_completed(futures):
            period = futures[future]
            try:
                results.append(future.result())
                print(json.dumps({'period': period, 'independent_cloud_audit': 'passed'}), flush=True)
            except Exception as error:
                errors.append({'period_end': period, 'error_class': type(error).__name__,
                    'error': str(error) if isinstance(error, ValueError) else 'Details redacted'})
    if sha(qualified_path) != qualified_sha256:
        raise ValueError('Runtime receipt changed')
    receipt = {'status': 'passed' if len(results) == 21 and not errors else 'stopped',
        'image': qualified['image'], 'runtime_qualified_sha256': sha(qualified_path),
        'months_verified': len(results), 'items': sorted(results, key=lambda row: row['period_end']),
        'errors': errors, 'visual_acceptance': False, 'source_data_changed': False, 'cloud_writes': 0}
    receipt['validator_sha256'] = validators
    receipt['audit_controller_sha256'] = sha(Path(__file__))
    write(output / 'receipt.json', receipt)
    print(json.dumps({'status': receipt['status'], 'months': len(results), 'errors': errors,
                      'visual_acceptance': False}), flush=True)
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
