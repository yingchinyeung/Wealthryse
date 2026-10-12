"""Source-bound worker diagnostics; saved workloads remain unchanged."""
import sys
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe

ROOT=Path(__file__).resolve().parents[1]
CANDIDATE=ROOT/'.work/platform-upgrade-20261009-stage3/worker/generate_month_end_package.py'
SOURCE=Path(__file__).with_name('probe_platform_worker_artifacts_20261009.py')
class BoundSource:
    def read_text(self,encoding):
        return 'WORKER_SOURCE = '+repr(CANDIDATE.read_text(encoding=encoding))+'\n'+SOURCE.read_text(encoding=encoding)
probe.SOURCE=BoundSource()
probe.OUT=ROOT/'.work/platform-upgrade-20261009-stage3/artifact-probe-v1'
if __name__=='__main__':
    if sys.argv[2:]==['v2']:
        SOURCE=SOURCE.with_name('probe_platform_worker_artifacts_v2_20261009.py')
        probe.OUT=ROOT/'.work/platform-upgrade-20261009-stage3/artifact-probe-v2'
    probe.main(sys.argv[1])
