import sys
import control_platform_assurance_probe_20261009 as control
control.SOURCE=control.ROOT/'scripts/probe_crisis_coverage_20261009.py'
control.OUT=control.ROOT/'.work/platform-upgrade-20261009-stage5/crisis-coverage-v2'
if __name__=='__main__':control.main(sys.argv[1])
