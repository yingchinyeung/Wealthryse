"""Reuse strictly read-only assurance diagnostics against the installed website."""
import sys
import control_platform_assurance_probe_20261009 as control
control.OUT=control.ROOT/'.work/platform-upgrade-20261009-stage5/assurance-live-v1'
if __name__=='__main__':control.main(sys.argv[1])
