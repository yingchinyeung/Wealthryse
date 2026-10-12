"""Scoped stage-two diagnostics using the existing transient-job controller."""
import sys
import json
import hashlib
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261009-stage2'
WORKER_IMAGE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:af15770889dc589e493e496cc92334c2296f16ae1bc9f7869d2a2eb50852f1a9'


if __name__ == '__main__':
    stage, kind = sys.argv[1:]
    if kind == 'worker-baseline':
        probe.SOURCE = Path(__file__).with_name('probe_platform_worker_baseline_20261009.py')
        probe.OUT = OUT / 'worker-baseline-v1'
        probe.ROLLBACK = True
        probe.release.IMAGE = WORKER_IMAGE
    elif kind in ('worker-postgres', 'worker-postgres-v2'):
        source = Path(__file__).with_name('probe_platform_worker_postgres_20261009.py')
        candidate = ROOT / '.work/platform-upgrade-20261009-v1/worker/generate_month_end_package.py'
        if kind == 'worker-postgres-v2':
            candidate = OUT / 'worker/generate_month_end_package.py'
        # Source assembly only; no execution or local candidate edits.
        class BoundSource:
            def read_text(self, encoding):
                return 'WORKER_SOURCE = ' + repr(candidate.read_text(encoding=encoding)) + '\n' + source.read_text(encoding=encoding)
        probe.SOURCE = BoundSource()
        probe.OUT = OUT / ('worker-postgres-v2' if kind == 'worker-postgres-v2' else 'worker-postgres-v1')
    else:
        raise ValueError('Unknown bounded stage')
    OUT.mkdir(exist_ok=True)
    probe.main(stage)
