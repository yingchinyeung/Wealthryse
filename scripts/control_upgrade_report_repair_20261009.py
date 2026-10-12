"""Bound exact original-version evidence to the existing diagnostic identity."""
import sys,json
from pathlib import PurePosixPath
import control_platform_assurance_probe_20261009 as control
control.OUT=control.ROOT/'.work/platform-upgrade-20261009-stage8/report-repair-qualification-v1'
source=control.ROOT/'scripts/qualify_upgrade_report_repair_20261009.py'
evidence=json.loads((control.ROOT/'.work/platform-upgrade-20261009-stage8/report-version-recovery-v2/result.json').read_bytes())
if not evidence['passed'] or len(evidence['repairs'])!=375:raise ValueError('Exact recovered originals required')
repairs=[]
for row in evidence['repairs']:
    p=PurePosixPath(row['output_reference'])
    if len(p.parts)!=5 or p.parts[:4]!=('internal-reports','review',row['subject_id'],row['period_end']):raise ValueError('Unexpected original path')
    recovery=row['recovery']
    repairs.append({**{k:row[k] for k in ('generation_run_id','subject_id','period_end','output_reference','output_sha256')},
        'source_version':recovery['source_version'],'recovered_reference':str(p.with_name(p.stem+'__recovered_'+row['output_sha256']+p.suffix))})
class BoundSource:
    def read_text(self,encoding):return 'REPAIRS = '+repr(repairs)+'\n'+source.read_text(encoding=encoding)
control.SOURCE=BoundSource()
if __name__=='__main__':control.main(sys.argv[1])
