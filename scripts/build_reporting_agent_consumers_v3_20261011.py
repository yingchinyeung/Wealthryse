"""Fresh builds exclude local Python bytecode; earlier native evidence retained."""
import shutil
import sys
from pathlib import Path
import build_reporting_agent_consumers_20261011 as old


def prepare(kind):
    before = old.STAGE / ('consumer-agent-' + kind + '-v2')
    after = old.STAGE / ('consumer-agent-' + kind + '-v3')
    if after.exists(): raise ValueError('Preserve previous clean-context candidate')
    source = old.h.read(before / 'source-intent.json')
    native = old.h.read(before / 'native-build-qualified.json')
    files = {p.relative_to(before/'context').as_posix(): old.h.sha(p) for p in (before/'context').rglob('*') if p.is_file()}
    extra = set(files)-set(source['files_sha256'])
    expected = {'payload/src/ibkr_etl/__pycache__/agent_tools.cpython-314.pyc',
                'payload/src/ibkr_etl/__pycache__/reporting_currency_holdings.cpython-314.pyc'}
    if extra != expected or any(files.get(n)!=h for n,h in source['files_sha256'].items()) or not native['qualified'] or native['linux_tests']!=33:
        raise ValueError('Unexpected frozen-context discrepancy')
    if not (before/'scan-preflight-failure.json').exists():
        old.h.save(before/'scan-preflight-failure.json', {'passed': False, 'reason': 'Native tests passed; scan preflight rejected two undeclared local bytecode cache files in the qualification context.',
                   'extra_files': {n: files[n] for n in sorted(extra)}, 'deployed': False,
                   'resolution': 'Separate v3 build with cache-free source context; original v2 files retained.'})
    shutil.copytree(before/'payload', after/'payload', ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copyfile(before/'prepared.json', after/'prepared.json')


if __name__ == '__main__':
    stage, kind = sys.argv[1:]
    if kind not in ('web','assurance'): raise ValueError('Exact two consumer scopes required')
    if stage=='prepare': prepare(kind)
    source=Path(old.__file__).read_text()
    for before, after in (("('consumer-agent-' + kind + '-v1')", "('consumer-agent-' + kind + '-v3')"),
                          ("kind + '-20261011-v1'", "kind + '-20261011-v3'")):
        if source.count(before)!=1: raise ValueError('Exact clean-build anchor required')
        source=source.replace(before,after)
    exec(compile(source,'scoped-agent-consumer-v3','exec'))
