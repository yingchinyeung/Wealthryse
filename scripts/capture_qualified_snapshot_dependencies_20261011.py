"""Inspect source dependencies from the already qualified reporter image offline."""
import ast
import json
from pathlib import Path
import capture_reporting_consumers_offline_20261011 as old
import scan_ms_presentation_worker_candidate as scan

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261011-stage38/qualified-source-v1'

def main():
    _, _, image = scan.verify(ROOT / '.work/platform-upgrade-20261011-stage34/reporting-image-v5')
    qualified = image['registry'] + '/' + image['repository'] + '@' + image['digest']
    source = Path(old.__file__).read_text()
    changes = {
        "out = STAGE / ('consumer-source-' + kind + '-v1')": 'out = OUT',
        "job, location = TARGETS[kind]": "job, location = 'qualified-monthly-reporter', 'platform-upgrade-20261011-stage34/reporting-image-v5'",
        "row = next(r for r in mapping['workloads'] if r['name'] == job)": "row = {'images': [QUALIFIED], 'entrypoints': ['scripts/freeze_month_end_snapshot.py']}",
        "names.add('src/ibkr_etl/web.py')": "names.update(p.relative_to(ROOT).as_posix() for p in (ROOT/'src/ibkr_etl').glob('*.py'))",
    }
    for before, after in changes.items():
        if source.count(before) != 1:
            raise ValueError('Reviewed extraction anchor changed')
        source = source.replace(before, after)
    scope = {'__name__': 'qualified_dependency_capture', '__file__': old.__file__, 'OUT': OUT, 'QUALIFIED': qualified}
    exec(compile(source, 'qualified_dependency_capture', 'exec'), scope)
    scope['capture']('snapshot')
    closure = set()
    pending = ['reporting_snapshot']
    while pending:
        name = pending.pop()
        if name in closure:
            continue
        file = OUT / 'src/ibkr_etl' / (name + '.py')
        if not file.is_file():
            raise ValueError('Dependency not captured: ' + name)
        closure.add(name)
        for node in ast.walk(ast.parse(file.read_text(encoding='utf-8'))):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                pending.append(node.module.split('.')[0])
    installed = json.loads((OUT.parent/'snapshot-source-v1/result.json').read_bytes())['files_sha256']
    qualified_hashes = json.loads((OUT/'result.json').read_bytes())['files_sha256']
    changed = [n for n in sorted(closure) if installed.get('src/ibkr_etl/'+n+'.py') != qualified_hashes['src/ibkr_etl/'+n+'.py']]
    print(json.dumps({'dependency_closure': sorted(closure), 'changed_modules': changed}))
    (OUT/'dependency-closure.json').write_text(json.dumps({'closure': sorted(closure), 'changed': changed}, indent=2), encoding='utf-8')

if __name__ == '__main__':
    main()
