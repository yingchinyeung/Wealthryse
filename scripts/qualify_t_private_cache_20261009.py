"""Replay all T monthly core caches privately; no database connections or writes."""
from pathlib import Path
import hashlib
import json
from ibkr_etl.alpha_world_t_cache import build, validate

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '.work/alpha-world-t-onboarding-20261008/history-v1/snapshots'
OUT = ROOT / '.work/alpha-t-core-cache-qualification-20261009-v1'


def main():
    OUT.mkdir(exist_ok=False)
    rows = []
    paths = sorted(SOURCE.glob('*.json'))
    assert len(paths) == 21
    for path in paths:
        raw = path.read_bytes()
        source = json.loads(raw)
        record = build(source)
        assert validate(record, as_of=path.stem, current_snapshot=source)
        assert record == build(source)
        with (OUT / path.name).open('x', encoding='utf-8') as stream:
            json.dump(record, stream, indent=2, allow_nan=False)
        assert path.read_bytes() == raw
        rows.append({'period_end': path.stem, 'status': 'passed',
            'input_sha256': hashlib.sha256(raw).hexdigest(),
            'payload_sha256': record['payload_sha256'],
            'core_metric_count': len(record['payload']['metrics']),
            'nav_observations': record['payload']['analytics_evidence']['nav_observations'],
            'daily_return_observations': record['payload']['analytics_evidence']['daily_return_observations']})
    result = {'status': 'private_core_cache_qualified', 'months': len(rows), 'results': rows,
        'production_integrated': False, 'production_writes': False, 'publication_allowed': False,
        'unavailable': ['benchmark comparison', 'crisis price validation', 'extended risk integration'],
        'code_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                       for name in ('src/ibkr_etl/alpha_world_t_cache.py', 'src/ibkr_etl/alpha_world_t_analytics.py',
                                    'src/ibkr_etl/risk_snapshots.py')}}
    with (OUT / 'summary.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != 'results'}))


if __name__ == '__main__':
    main()
