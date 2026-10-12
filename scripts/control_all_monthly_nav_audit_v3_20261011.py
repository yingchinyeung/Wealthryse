"""Repeat the method-aware audit after the one-month private correction."""
import sys
import control_all_monthly_nav_audit_v2_20261011 as wrapper
c=wrapper.c
c.OUT=c.OUT.parent/'all-monthly-nav-audit-v3'
c.base.OUT=c.base.OUT.parent/'august-snapshot-correction-v1'
previous=c.hashes
def hashes():
    values=previous();values['control_all_monthly_nav_audit_v3_20261011.py']=c.hashlib.sha256((c.ROOT/'scripts/control_all_monthly_nav_audit_v3_20261011.py').read_bytes()).hexdigest();return values
c.hashes=hashes
if __name__=='__main__':c.main(*sys.argv[1:])
