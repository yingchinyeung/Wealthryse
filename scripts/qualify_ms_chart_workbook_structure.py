"""Independent package/geometry checks on private repaired MS decks and smoke output."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SKILL = Path('C:/Users/antho/.codex/plugins/cache/openai-primary-runtime/presentations/26.1007.11041/skills/presentations')
PYTHON = Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe')
WARNINGS = {('object_outside_slide_boundary', slide, identity) for slide, identity in
            ((1, '125'), (1, '2'), (1, '121'), (2, '37'), (20, '5'), (20, '7'), (20, '11'))}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    folder = args.folder.resolve()
    qualification = json.loads((folder / 'local-qualification.json').read_bytes())
    if qualification['status'] != 'isolated_local_candidate_passed' or qualification['replay']['months_verified'] != 21:
        raise ValueError('All-month immutable local qualification required')
    stage = folder / 'independent-local-structure-v1'
    stage.mkdir(exist_ok=False)
    tasks = [(folder / 'local-repaired-cloud-outputs' / ('AlphaWorld_MS_Presentation_' + row['period_end'] + '.pptx'),
              row['validation']['output_sha256'], row['period_end']) for row in qualification['replay']['items']]
    smoke = folder / 'September_Cloud_Path_Smoke.pptx'
    tasks.append((smoke, sha(smoke), 'fresh-september-cloud-path'))

    def check(task):
        path, expected, name = task
        if sha(path) != expected:
            raise ValueError('Output binding differs')
        commands = {
            'package': [str(PYTHON), str(SKILL / 'container_tools/inspect_presentation_package_integrity.py'), str(path), '--fail-on-findings'],
            'layout': [str(PYTHON), str(SKILL / 'container_tools/inspect_presentation_layout_geometry.py'), str(path),
                       '--fail-on-findings', '--expected-slide-size-emu', '9144000,5143500', '--expected-slide-count', '20',
                       '--validate-bullet-geometry', '--validate-heading-fit',
                       *[v for slide in (3,5,6,9,10,11,12,13,14,15,16) for v in ('--require-native-table-slide', str(slide))]]}
        for kind, command in commands.items():
            result = subprocess.run(command, cwd=ROOT, env={**os.environ, 'PYTHONUTF8': '1'},
                                    capture_output=True, text=True, encoding='utf-8', timeout=180)
            report = json.loads(result.stdout)
            save(stage / (name + '-' + kind + '.json'), {'exit_code': result.returncode, 'report': report})
            if result.returncode or report['finding_count'] != 0:
                raise ValueError(name + '/' + kind + ' has findings')
            if kind == 'layout':
                actual = {(w['kind'], w.get('slide'), w.get('object_id')) for w in report.get('warnings', [])}
                if actual - WARNINGS:
                    raise ValueError('New layout warning requires review')
        if sha(path) != expected:
            raise ValueError('Output changed during inspection')
        return {'period': name, 'output_sha256': expected, 'package_findings': 0, 'layout_findings': 0}

    rows, errors = [], []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(check, task): task[2] for task in tasks}
        for future in as_completed(futures):
            try:
                rows.append(future.result())
            except Exception as error:
                errors.append({'period': futures[future], 'error_class': type(error).__name__,
                               'error': str(error) if isinstance(error, ValueError) else 'Details redacted'})
    passed = len(rows) == 22 and not errors
    record = {'status': 'passed' if passed else 'stopped', 'documents_checked': len(rows), 'items': rows, 'errors': errors,
              'local_qualification_sha256': sha(folder / 'local-qualification.json'),
              'source_intent_sha256': sha(folder / 'source-intent.json'),
              'fresh_render_path': 'No SKILL_DIR or desktop finalizer',
              'package_validator_sha256': sha(SKILL / 'container_tools/inspect_presentation_package_integrity.py'),
              'layout_validator_sha256': sha(SKILL / 'container_tools/inspect_presentation_layout_geometry.py'),
              'visual_acceptance': False, 'azure_changed': False, 'source_data_changed': False}
    save(folder / 'local-structure-qualification.json', record)
    print(json.dumps({'status': record['status'], 'documents_checked': len(rows), 'errors': errors}))
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
