"""Offline, hash-verified source inventory of the frozen snapshot image only."""
from pathlib import Path
import capture_reporting_consumers_offline_20261011 as old

def main():
    source = Path(old.__file__).read_text()
    changes = {
        "out = STAGE / ('consumer-source-' + kind + '-v1')":
        "out = ROOT / '.work/platform-upgrade-20261011-stage38/snapshot-source-v1'",
        "names.add('src/ibkr_etl/web.py')":
        "names.update(p.relative_to(ROOT).as_posix() for p in (ROOT/'src/ibkr_etl').glob('*.py'))\n    names.update({'ALPHA_T_RUNTIME_RELEASE.json','scripts/check_report_runtime.py'})",
    }
    for before, after in changes.items():
        if source.count(before) != 1:
            raise ValueError('Reviewed extraction anchor changed')
        source = source.replace(before, after)
    scope = {'__name__': 'snapshot_dependency_capture', '__file__': old.__file__}
    exec(compile(source, 'snapshot_dependency_capture', 'exec'), scope)
    scope['capture']('snapshot')

if __name__ == '__main__':
    main()
