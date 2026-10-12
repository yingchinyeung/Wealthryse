"""Exact-image real-process rehearsal, with read-only database safeguard."""
import sys
import control_monthly_entrypoint_v3_20261009 as wrapper
controller=wrapper.controller
controller.SOURCE=controller.SOURCE.with_name('probe_monthly_worker_real_process_20261009.py')
controller.probe.OUT=wrapper.release.BUNDLE/'actual-process-probe-v1'
if __name__=='__main__':controller.probe.main(sys.argv[1])
