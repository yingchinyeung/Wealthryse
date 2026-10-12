"""Fresh policy tests after the generic full-portfolio-history contract change."""
import qualify_reporting_release_policy_20261011 as c
c.OUT=c.OUT.parent/'release-policy-v3'
if __name__=='__main__': c.main()
