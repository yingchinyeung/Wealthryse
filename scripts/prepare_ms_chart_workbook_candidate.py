"""Freeze only the workbook delta over the verified r2 reporting candidate.

No broad repository build context, production cutover, source or Azure writes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import prepare_ms_presentation_worker_candidate as base

ROOT = base.ROOT
PARENT = ROOT / '.work/alpha-ms-presentation-worker-candidate-20261007-r2'
PARENT_INTENT_SHA = 'e91aa875788ce31307af24e02ff6b04b3ee389c24ef510dbabcf47d384309193'
DELTA = ('scripts/render_monthly_presentation.mjs', 'scripts/validate_subject_presentation.py',
         'src/ibkr_etl/presentation_design.py', 'src/ibkr_etl/ms_chart_workbooks.py',
         'tests/test_subject_presentation_validation.py', 'tests/test_ms_chart_workbooks.py')


def save(path, record):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2, allow_nan=False)


def prepare(folder):
    if folder.exists():
        raise ValueError('Preserve existing candidate; choose a new folder')
    if base.sha(PARENT / 'source-intent.json') != PARENT_INTENT_SHA:
        raise ValueError('Parent frozen intent differs')
    parent = json.loads((PARENT / 'source-intent.json').read_bytes())
    frozen = {p.relative_to(PARENT / 'context').as_posix(): base.sha(p)
              for p in (PARENT / 'context').rglob('*') if p.is_file()}
    if frozen != parent['files_sha256'] or parent['base_image'] != base.IMAGE:
        raise ValueError('Parent context or baseline image differs')
    qualified = json.loads((PARENT / 'local-qualification.json').read_bytes())
    if qualified['status'] != 'isolated_local_candidate_passed' or qualified['source_intent_sha256'] != PARENT_INTENT_SHA:
        raise ValueError('Verified parent local qualification required')
    files = {name: (PARENT / 'context' / name).read_bytes() for name in frozen}
    for name in DELTA:
        content = (ROOT / name).read_bytes()
        if name.endswith('.mjs'):
            text = content.decode('utf-8').replace('\r\n', '\n').replace(': row.description || row.symbol,', ': row.description,')
            # Reverse exactly this turn's delta and require the r2 source to match.
            prior = text.replace('let candidatePath =', 'const candidatePath =')
            start = prior.index('\nif (isMsHkd) {\n  const workbookPath =')
            end = prior.index('\nif (process.env.SKILL_DIR', start)
            prior = prior[:start] + prior[end:]
            start = prior.index('  const workbooks = JSON.parse(')
            end = prior.index('  const audit=JSON.parse(', start)
            prior = prior[:start] + prior[end:]
            if prior != files[name].decode('utf-8').replace('\r\n', '\n'):
                raise ValueError('Renderer contains changes outside the explicit workbook delta')
            content = text.encode('utf-8')
        files[name] = content
    design = files['src/ibkr_etl/presentation_design.py'].decode()
    prior_design = design.replace('alpha-world-ms-v24-hkd-hibor-evidence-v2', 'alpha-world-ms-v24-hkd-hibor-evidence-v1')
    prior_design = prior_design.replace('    "Six embedded chart-data workbooks match every category, value and series header.",\n', '')
    if prior_design != (PARENT / 'context/src/ibkr_etl/presentation_design.py').read_text(encoding='utf-8'):
        raise ValueError('Design contains changes beyond the declared MS contract')
    release = dict(parent['release'])
    release.update(content_contract_version='alpha-world-ms-v24-hkd-hibor-evidence-v2',
                   parent_source_intent_sha256=PARENT_INTENT_SHA, chart_workbooks_required=True,
                   workbook_lineage='Literal frozen chart data, not source workbook formulas')
    release['installed_files_sha256'] = {n: hashlib.sha256(data).hexdigest() for n, data in files.items()
                                       if n.startswith(('src/', 'scripts/'))}
    files['MS_PRESENTATION_WORKER_RELEASE.json'] = (json.dumps(release, indent=2) + '\n').encode()
    folder.mkdir(parents=True)
    for name, content in files.items():
        path = folder / 'context' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(content)
    # Keep installed-source provenance, not the unqualified repository snapshot.
    with (folder / 'baseline-binding.json').open('xb') as stream:
        stream.write((PARENT / 'baseline-binding.json').read_bytes())
    intent = {'status': 'scoped_local_candidate_frozen', 'base_image': base.IMAGE,
              'tag': 'ibkr-platform:' + folder.name,
              'files_sha256': {n: hashlib.sha256(data).hexdigest() for n, data in files.items()},
              'baseline_binding_sha256': base.sha(folder / 'baseline-binding.json'),
              'parent_source_intent_sha256': PARENT_INTENT_SHA, 'declared_delta': list(DELTA),
              'expected_test_count': 83, 'preparer_sha256': base.sha(Path(__file__)),
              'release': release, 'build_dispatched': False, 'azure_changed': False}
    save(folder / 'source-intent.json', intent)
    print(json.dumps({'status': intent['status'], 'files': len(files), 'expected_tests': 83, 'azure_changed': False}))


def qualify(folder):
    runtime = folder / 'local-runtime'
    intent_path = folder / 'source-intent.json'
    intent = json.loads(intent_path.read_bytes())
    binding = json.loads((folder / 'local-runtime-binding.json').read_bytes())
    actual = {p.relative_to(runtime).as_posix(): base.sha(p) for p in runtime.rglob('*')
              if p.is_file() and '__pycache__' not in p.parts}
    if actual != binding['files_sha256']:
        raise ValueError('Isolated runtime changed')
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(('src', 'scripts')), 'PYTHONUTF8': '1'}
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                            cwd=runtime, env=env, capture_output=True, text=True, encoding='utf-8', timeout=180)
    log = result.stdout + result.stderr
    with (folder / 'local-test.log').open('x', encoding='utf-8') as stream:
        stream.write(log)
    if result.returncode or not re.search(r'Ran ' + str(intent['expected_test_count']) + r' tests\b', log) or '\nOK' not in log:
        raise ValueError('Isolated tests did not pass the exact expected count')
    # Isolated subprocess ensures no repository import fallback can pass replay.
    code = """import json,sys,hashlib
from pathlib import Path
from ibkr_etl.ms_chart_workbooks import materialize
from validate_subject_presentation import validate_subject_presentation
root=Path(sys.argv[1]); out=Path(sys.argv[2]); out.mkdir()
proof=json.loads((root/'.work/alpha-ms-presentation-worker-candidate-20261007-r2/runtime-v1/candidate-qualified.json').read_bytes())
rows=[]
for item in proof['proof']['artifacts']:
    date=item['period_end']; source=root/'.work/alpha-ms-presentation-worker-candidate-20261007-r2/runtime-v1/candidate-outputs'/('AlphaWorld_MS_Presentation_'+date+'.pptx')
    if hashlib.sha256(source.read_bytes()).hexdigest()!=item['presentation_sha256']: raise ValueError('r2 cloud source differs')
    target=out/source.name; receipt=materialize(source,target)
    snapshot=root/'.work/alpha-ms-presentation-hkd-20261007/inputs'/(date+'.json')
    gate=validate_subject_presentation(target,snapshot,subject_id='alpha-world-ms',period_end=date,expected_strategy='iStrategy: Alpha World MS')
    rows.append({'period_end':date,'materialization':receipt,'validation':gate})
print(json.dumps({'months_verified':len(rows),'items':rows}))
"""
    result = subprocess.run([sys.executable, '-c', code, str(ROOT), str(folder / 'local-repaired-cloud-outputs')],
                            cwd=runtime, env=env, capture_output=True, text=True, encoding='utf-8', timeout=180)
    if result.returncode:
        raise ValueError('Isolated workbook replay failed: ' + result.stderr[-1800:])
    replay = json.loads(result.stdout)
    if replay['months_verified'] != 21:
        raise ValueError('All 21 months required')
    save(folder / 'local-qualification.json', {
        'status': 'isolated_local_candidate_passed', 'base_image': intent['base_image'],
        'source_intent_sha256': base.sha(intent_path), 'local_runtime_binding_sha256': base.sha(folder / 'local-runtime-binding.json'),
        'tests_passed': intent['expected_test_count'], 'test_log_sha256': base.sha(folder / 'local-test.log'),
        'replay': replay, 'template_sha256': base.sha(runtime / 'templates/U16088571/approved-august-2026-20-slide-v24.pptx'),
        'source_data_changed': False, 'azure_changed': False, 'visual_acceptance': False,
        'scope': 'Repair and independent shared-gate replay, not a new cloud render'})
    print(json.dumps({'isolated_tests_passed': intent['expected_test_count'], 'months_verified': 21,
                      'chart_workbooks_verified': 126, 'azure_changed': False}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('prepare', 'runtime', 'qualify'))
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    if args.stage == 'prepare':
        prepare(args.folder.resolve())
    elif args.stage == 'runtime':
        base.materialize_local_runtime(args.folder.resolve())
    else:
        qualify(args.folder.resolve())


if __name__ == '__main__':
    main()
