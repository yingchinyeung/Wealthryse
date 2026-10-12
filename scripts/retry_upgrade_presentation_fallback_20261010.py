"""Preserved failed probe; reproduce normal script-directory import semantics."""
import json,sys
from pathlib import Path
import control_upgrade_full_legacy_20261010 as m

OUT=m.OUT/'presentation-fallback-retry-v2';TARGET='legacypresentation'


def main(stage):
    m.configure();original=m.probe_source
    def source(target,expected):
        if target!=TARGET:raise ValueError('Exact presentation fallback only')
        return "import sys;sys.path.insert(0,'/app/scripts')\n"+original(target,expected)
    m.base.OUT=OUT;m.base.probe_source=source
    if stage in ('freeze','start','read'):return m.base.runtime(TARGET,stage,'fallback')
    if stage!='seal':raise ValueError('Unknown stage')
    root=OUT/(TARGET+'-runtime-v1');result=m.c.protected(root/'fallback/result.json')
    image,definition,proofs=m.candidate(TARGET);m.base.validate_trial(result,definition['base_image'],proofs['baseline'])
    old=m.OUT/(TARGET+'-runtime-v1/fallback');failure=m.c.protected(old/'failure.json')
    if failure.get('passed') is not False:raise ValueError('Original failure required')
    # Create a completion pointing explicitly to the successful new execution;
    # retain the initial failed intent, dispatch and failure without modification.
    m.c.save(old,'result',{k:v for k,v in {**result,'initial_failed_execution':failure['execution'],
        'successful_retry_receipt':result['protected_receipt'],'retry_controller_sha256':m.base.hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'failure_cause':'runpy diagnostic omitted normal script-directory sys.path; installed application unchanged',
        'original_failure_preserved':True}.items() if k!='protected_receipt'})
    print(json.dumps({'passed':True,'successful_retry':result['execution'],'original_failure_preserved':True}))


if __name__=='__main__':main(*sys.argv[1:])
