"""Freeze and privately qualify only number formats over the installed r5 image.

Create-only evidence, isolated imports, no Azure or source-data mutations.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / '.work/alpha-ms-presentation-worker-candidate-20261007-r5'
PARENT_SHA = 'a9be816aff6d1416857c0ba5e5d72a869e920d94ec1073ca8c21c296625cb370'
IMAGE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:d98e16415d7e4b601571913f2d3be3bc48e62c69d9725419f556a2b71333a584'
REVIEW = ROOT / '.work/alpha-ms-number-formats-20261008-v2'
REVIEW_SHA = 'a217a02c02177fe9f7ab54026aff13829974d25877f541e4657acedb17ac9aa9'
DELTA = ('src/ibkr_etl/ms_chart_workbooks.py', 'scripts/validate_subject_presentation.py',
         'tests/test_ms_chart_workbooks.py', 'tests/test_subject_presentation_validation.py',
         'scripts/verify_ms_office_format_roundtrip.py', 'tests/test_ms_office_format_roundtrip.py',
         'tests/test_ms_presentation_worker_runtime.py')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(folder):
    return {p.relative_to(folder).as_posix(): sha(p) for p in folder.rglob('*')
            if p.is_file() and '__pycache__' not in p.parts}


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def checked_parent():
    if sha(PARENT / 'source-intent.json') != PARENT_SHA:
        raise ValueError('Parent intent differs')
    parent = json.loads((PARENT / 'source-intent.json').read_bytes())
    if inventory(PARENT / 'context') != parent['files_sha256']:
        raise ValueError('Parent frozen source differs')
    built = json.loads((PARENT / 'build-completed.json').read_bytes())
    native = json.loads((PARENT / 'native-build-qualified.json').read_bytes())
    if (built['source_intent_sha256'] != PARENT_SHA or built['native_status'] != 'Succeeded'
            or len(built['output_images']) != 1
            or built['output_images'][0]['digest'] != IMAGE.split('@')[1]
            or not native['qualified'] or native['run'] != built['run']
            or sha(PARENT / 'native-build.log') != native['native_log_sha256']):
        raise ValueError('Parent build provenance differs')
    return parent


def prepare(folder):
    if folder.exists():
        raise ValueError('Preserve prior attempts; use a new candidate folder')
    parent = checked_parent()
    if sha(REVIEW / 'review-acceptance.json') != REVIEW_SHA:
        raise ValueError('Private review receipt differs')
    review = json.loads((REVIEW / 'review-acceptance.json').read_bytes())
    if (review['months'] != 21 or review['financial_charts_verified'] != 126
            or review['risk_metrics_verified'] != 147):
        raise ValueError('All-month private review required')
    for name, field in (('qualification.json', 'candidate_proof_sha256'),
                        ('all-month-previews/comparison.json', 'preview_comparison_sha256')):
        if sha(REVIEW / name) != review[field]:
            raise ValueError('Private review dependency differs')
    files = {name: (PARENT / 'context' / name).read_bytes() for name in parent['files_sha256']}
    for name in DELTA:
        files[name] = (ROOT / name).read_bytes()
    runtime_test = files['tests/test_ms_presentation_worker_runtime.py'].decode().replace('\r\n', '\n')
    if runtime_test.replace(IMAGE.split('@')[1], parent['base_image'].split('@')[1]) != (PARENT / 'context/tests/test_ms_presentation_worker_runtime.py').read_text(encoding='utf-8'):
        raise ValueError('Runtime test differs beyond the exact base-image pin')
    # The validation change must be exactly the new strict format requirement.
    new_gate = files['scripts/validate_subject_presentation.py'].decode().replace('\r\n', '\n')
    old_gate = (PARENT / 'context/scripts/validate_subject_presentation.py').read_text(encoding='utf-8')
    if new_gate.replace('verify_ms_chart_workbooks(output, require_format=True)', 'verify_ms_chart_workbooks(output)') != old_gate:
        raise ValueError('Shared validator contains unrelated changes')
    module = files['src/ibkr_etl/ms_chart_workbooks.py'].decode().replace('\r\n', '\n')
    reviewed_module = module.replace('def verify_ms_chart_workbooks(path, *, require_format=False):',
                                    'def verify_ms_chart_workbooks(path):').replace(
        'return verify_bytes(path.read_bytes(), require_format=require_format)', 'return verify_bytes(path.read_bytes())')
    if hashlib.sha256(reviewed_module.encode()).hexdigest() != '61ea49d11df32b803ca79e5a541710db67881817bf8436bb017bb81de806636c':
        raise ValueError('Workbook code differs beyond the reviewed format fix and strict gate')
    docker = files['Dockerfile'].decode()
    if not docker.startswith('FROM ' + parent['base_image'] + '\n'):
        raise ValueError('Unexpected parent Dockerfile')
    files['Dockerfile'] = docker.replace('FROM ' + parent['base_image'], 'FROM ' + IMAGE, 1).encode()
    release = dict(parent['release'])
    release.update(base_image=IMAGE, parent_source_intent_sha256=PARENT_SHA,
                   chart_number_formats_required=True, hkd_editor_column_width=16,
                   private_format_review_sha256=REVIEW_SHA)
    release['installed_files_sha256'] = {name: hashlib.sha256(data).hexdigest()
        for name, data in files.items() if name.startswith(('src/', 'scripts/'))}
    files['MS_PRESENTATION_WORKER_RELEASE.json'] = (json.dumps(release, indent=2) + '\n').encode()
    expected = sum(sum(isinstance(node, ast.FunctionDef) and node.name.startswith('test_')
                       for node in ast.walk(ast.parse(data.decode())))
                   for name, data in files.items() if name.startswith('tests/') and name.endswith('.py'))
    folder.mkdir(parents=True)
    for name, data in files.items():
        path = folder / 'context' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)
    save(folder / 'baseline-binding.json', {
        'base_image': IMAGE, 'parent_source_intent_sha256': PARENT_SHA,
        'parent_build_completed_sha256': sha(PARENT / 'build-completed.json'),
        'parent_native_build_qualified_sha256': sha(PARENT / 'native-build-qualified.json'),
        'live_image_read_only_verified_on': '2026-10-08',
        'upstream_baseline_binding_sha256': sha(PARENT / 'baseline-binding.json')})
    save(folder / 'source-intent.json', {
        'status': 'scoped_local_candidate_frozen', 'base_image': IMAGE,
        'tag': 'ibkr-platform:' + folder.name,
        'files_sha256': inventory(folder / 'context'),
        'baseline_binding_sha256': sha(folder / 'baseline-binding.json'),
        'parent_source_intent_sha256': PARENT_SHA, 'declared_delta': list(DELTA),
        'expected_test_count': expected, 'preparer_sha256': sha(Path(__file__)),
        'private_format_review_sha256': REVIEW_SHA,
        'owner_native_open_confirmation_sha256': sha(REVIEW / 'owner-native-open-confirmation.json'),
        'release': release, 'azure_changed': False, 'build_dispatched': False})
    print(json.dumps({'status': 'scoped_local_candidate_frozen', 'files': len(files),
                      'expected_test_count': expected, 'azure_changed': False}))


def runtime(folder):
    checked_parent()
    intent = json.loads((folder / 'source-intent.json').read_bytes())
    if (inventory(folder / 'context') != intent['files_sha256']
            or sha(Path(__file__)) != intent['preparer_sha256']):
        raise ValueError('Candidate frozen source differs')
    parent_runtime = PARENT / 'local-runtime'
    binding = json.loads((PARENT / 'local-runtime-binding.json').read_bytes())
    parent_files = inventory(parent_runtime)
    extra = set(parent_files) - set(binding['files_sha256'])
    if (any(parent_files.get(name) != digest for name, digest in binding['files_sha256'].items())
            or any(not name.startswith('.work/presentation-validation/') for name in extra)):
        raise ValueError('Isolated parent runtime differs')
    target = folder / 'local-runtime'
    target.mkdir(exist_ok=False)
    for source, names in ((parent_runtime, binding['files_sha256']), (folder / 'context', intent['files_sha256'])):
        for name in names:
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((source / name).read_bytes())
    save(folder / 'local-runtime-binding.json', {
        'base_image': IMAGE, 'parent_runtime_binding_sha256': sha(PARENT / 'local-runtime-binding.json'),
        'files_sha256': inventory(target), 'repository_fallback_allowed': False})
    print(json.dumps({'isolated_runtime_prepared': True, 'repository_fallback_allowed': False}))


def qualify(folder):
    intent = json.loads((folder / 'source-intent.json').read_bytes())
    target = folder / 'local-runtime'
    binding = json.loads((folder / 'local-runtime-binding.json').read_bytes())
    if inventory(target) != binding['files_sha256'] or inventory(folder / 'context') != intent['files_sha256']:
        raise ValueError('Isolated candidate differs')
    temp = folder / 'test-temp'
    temp.mkdir(exist_ok=False)
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(('src', 'scripts')), 'PYTHONUTF8': '1',
           'TEMP': str(temp), 'TMP': str(temp)}
    tests = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
        cwd=target, env=env, capture_output=True, text=True, encoding='utf-8', timeout=180)
    log = tests.stdout + tests.stderr
    with (folder / 'local-test.log').open('x', encoding='utf-8') as stream:
        stream.write(log)
    if tests.returncode or not re.search(r'Ran ' + str(intent['expected_test_count']) + r' tests\b', log) or '\nOK' not in log or 'skipped=' in log:
        raise ValueError('Exact isolated regression did not pass')
    code = """import json,sys,hashlib
from pathlib import Path
from ibkr_etl.ms_chart_workbooks import upgrade_number_formats
from validate_subject_presentation import validate_subject_presentation
root=Path(sys.argv[1]); folder=Path(sys.argv[2]); out=folder/'local-repaired-cloud-outputs'; out.mkdir()
receipt=root/'.work/alpha-ms-presentation-worker-candidate-20261007-r5/registered-batch-v1/website-downloads-v1/acceptance.json'
if hashlib.sha256(receipt.read_bytes()).hexdigest()!='9a982281097f12f2ebfe5250d66d2ee15c197cbac4c81366700ea89226003acd': raise ValueError('Accepted source inventory differs')
proof=json.loads(receipt.read_bytes()); downloads=Path('C:/Users/antho/Downloads')
for item in proof['items']:
    if hashlib.sha256((downloads/item['filename']).read_bytes()).hexdigest()!=item['sha256']: raise ValueError('Accepted download differs')
review=root/'.work/alpha-ms-number-formats-20261008-v2'
accepted=json.loads((review/'review-acceptance.json').read_bytes())
expected={r['period_end']:r['presentation_sha256'] for r in accepted['items']}
rows=[]
for item in proof['items']:
    if item['report_type']!='presentation': continue
    date=item['period_end']; source=downloads/item['filename']
    deck=out/('AlphaWorld_MS_Presentation_'+date+'.pptx'); conversion=upgrade_number_formats(source,deck)
    accepted_deck=review/'presentations'/('MS_'+date+'_NumberFormats.pptx')
    if hashlib.sha256(accepted_deck.read_bytes()).hexdigest()!=expected[date] or deck.read_bytes()!=accepted_deck.read_bytes(): raise ValueError('Fresh materialization differs from visually reviewed deck')
    snapshot=root/'.work/alpha-ms-presentation-hkd-20261007/inputs'/(date+'.json')
    validation=validate_subject_presentation(deck,snapshot,subject_id='alpha-world-ms',period_end=date,expected_strategy='iStrategy: Alpha World MS')
    rows.append({'period_end':date,'materialization':conversion,'validation':validation,'exact_reviewed_package_identity':True})
print(json.dumps({'months_verified':len(rows),'items':rows}))
"""
    replay = subprocess.run([sys.executable, '-c', code, str(ROOT), str(folder)], cwd=target,
        env=env, capture_output=True, text=True, encoding='utf-8', timeout=180)
    if replay.returncode:
        raise ValueError('Isolated replay failed: ' + replay.stderr[-1800:])
    record = json.loads(replay.stdout)
    if record['months_verified'] != 21:
        raise ValueError('All 21 months required')
    save(folder / 'local-qualification.json', {
        'status': 'isolated_local_candidate_passed', 'base_image': IMAGE,
        'source_intent_sha256': sha(folder / 'source-intent.json'),
        'local_runtime_binding_sha256': sha(folder / 'local-runtime-binding.json'),
        'tests_passed': intent['expected_test_count'], 'test_log_sha256': sha(folder / 'local-test.log'),
        'replay': record, 'private_review_sha256': REVIEW_SHA,
        'azure_changed': False, 'source_data_changed': False,
        'scope': 'Historical formatting upgrade replay with exact reviewed-package identity; fresh renderer qualification remains separate'})
    print(json.dumps({'tests_passed': intent['expected_test_count'], 'months_verified': 21,
                      'charts_verified': 126, 'risk_metrics_verified': 147, 'azure_changed': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('prepare', 'runtime', 'qualify'))
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    globals()[args.stage](args.folder.resolve())
