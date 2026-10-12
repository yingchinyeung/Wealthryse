"""Rebase only the qualified sector correction; preserve consumer-specific risk.

Local preparation only. No image build, deployment or database access.
"""
import ast
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / '.work/platform-upgrade-20261011-stage34'
AGENT = 'src/ibkr_etl/agent_tools.py'
HELPER = 'src/ibkr_etl/reporting_currency_holdings.py'
FUNCTION = '_sector_allocation'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def function(source):
    matches = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == FUNCTION]
    if len(matches) != 1 or matches[0].decorator_list:
        raise ValueError('One undecorated sector function required')
    return matches[0]


def transform(installed, baseline, qualified):
    old, known, new = function(installed), function(baseline), function(qualified)
    if ast.dump(old, include_attributes=False) != ast.dump(known, include_attributes=False):
        raise ValueError('Consumer sector baseline differs; independent review required')
    lines = installed.splitlines(keepends=True)
    replacement = qualified.splitlines(keepends=True)[new.lineno - 1:new.end_lineno]
    result = ''.join(lines[:old.lineno - 1] + replacement + lines[old.end_lineno:])
    original_rest = [ast.dump(n, include_attributes=False) for n in ast.parse(installed).body if n is not None and not (isinstance(n, ast.FunctionDef) and n.name == FUNCTION)]
    result_rest = [ast.dump(n, include_attributes=False) for n in ast.parse(result).body if not (isinstance(n, ast.FunctionDef) and n.name == FUNCTION)]
    if original_rest != result_rest or ast.dump(function(result), include_attributes=False) != ast.dump(new, include_attributes=False):
        raise ValueError('Unrelated consumer function changed')
    compile(result, 'agent_tools.py', 'exec')
    return result


def prepare(kind):
    if kind not in ('web', 'assurance'):
        raise ValueError('Exact two compatible agent consumers only')
    capture = STAGE / ('consumer-source-' + kind + '-v1')
    proof = json.loads((capture / 'result.json').read_bytes())
    out = STAGE / ('consumer-agent-' + kind + '-v1')
    if out.exists():
        raise ValueError('Preserve previous candidate')
    installed = (capture / AGENT).read_bytes()
    patch = json.loads((STAGE / 'reporting-image-v4/context/patch-manifest.json').read_bytes())
    baseline = (STAGE / 'installed-reporting-source-v1' / AGENT).read_bytes()
    qualified = (STAGE / 'reporting-image-v4/context/payload' / AGENT).read_bytes()
    helper = (STAGE / 'reporting-image-v4/context/payload' / HELPER).read_bytes()
    if not proof['passed'] or sha(installed) != proof['files_sha256'][AGENT] or sha(baseline) != patch['baseline'][AGENT] or sha(qualified) != patch['candidate'][AGENT] or sha(helper) != patch['candidate'][HELPER]:
        raise ValueError('Captured/qualified source integrity differs')
    if HELPER not in proof['requested_but_absent']:
        raise ValueError('Helper already exists; preserve it for separate review')
    changed = transform(installed.decode(), baseline.decode(), qualified.decode()).encode()
    files = {AGENT: changed, HELPER: helper}
    for name, data in files.items():
        path = out / 'payload' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)
    result = {'prepared': True, 'consumer': proof['consumer'], 'base_image': proof['image'],
              'baseline': {AGENT: sha(installed)}, 'candidate': {n: sha(d) for n, d in files.items()},
              'new_files': [HELPER], 'source_capture_sha256': sha((capture / 'result.json').read_bytes()),
              'scope': 'Only standalone sector currency normalization; existing benchmark, risk and dispatch fixes preserved exactly. No reporting contract, renderer, release policy or website source changes.',
              'deployed': False, 'native_qualification_required': True}
    with (out / 'prepared.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({'prepared': True, 'consumer': proof['consumer'], 'source_files': 2, 'other_functions_preserved': True}))


if __name__ == '__main__':
    prepare(sys.argv[1])
