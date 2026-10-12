"""Postflight on the installed scheduled job identity, with read-only override."""
import sys,json
import control_monthly_real_process_20261009 as test
from deploy_metadata_monitor import BASE,request
from build_ms_presentation_worker_candidate import invariant

probe=test.controller.probe
probe.JOB='ibkrv2stg-month-end-factsheet'
probe.PATH=BASE+'Microsoft.App/jobs/'+probe.JOB
probe.OUT=test.wrapper.release.BUNDLE/'installed-process-probe-v1'

if __name__=='__main__':
    root=test.wrapper.release.BUNDLE/'deployment-v2'
    verified=json.loads((root/'verified.json').read_bytes())
    plan=json.loads((root/'plan.json').read_bytes())
    job=request('GET',probe.PATH+probe.API)
    if job['properties']['template']['containers'][0]['image']!=verified['image'] or invariant(job)!=plan['invariant']:
        raise ValueError('Installed monthly worker drifted')
    probe.main(sys.argv[1])
