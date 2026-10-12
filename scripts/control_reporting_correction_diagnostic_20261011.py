"""Reuse exact source-bound private migration runner in read-only diagnostic mode."""
import sys
from pathlib import Path
import control_reporting_snapshot_migration_20261011 as c
from control_upgrade_legacy_recovery_probe_20261009 import protected
c.OUT=c.OUT.parent/'snapshot-migration-diagnostic-v1'
c.FILES.extend([Path(__file__).name,'diagnose_reporting_correction_replay_20261011.py'])
original=c.b.corrected_template
def template(job,code):
    failure=protected(c.OUT.parent/'snapshot-migration-v1/failure.json')
    if failure['proof'].get('stage')!='prepare' or failure['proof'].get('message')!='Correction differs from reconciled plan':raise ValueError('Unexpected migration failure')
    old=(c.ROOT/'scripts/rehearse_reporting_snapshot_migration_20261011.py').read_text(encoding='utf-8')
    if code.count(old)!=1:raise ValueError('Exact private runner required')
    return original(job,code.replace(old,(c.ROOT/'scripts/diagnose_reporting_correction_replay_20261011.py').read_text(encoding='utf-8')))
c.b.corrected_template=template
# The parent's strict write-rehearsal receipt validator is deliberately not
# reused: read separately with read_reporting_correction_diagnostic_20261011.
if __name__=='__main__':
    if sys.argv[1:]!=['start']:raise ValueError('Start only; use diagnostic reader')
    c.main('start')
