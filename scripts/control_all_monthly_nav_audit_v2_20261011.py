"""Method-aware independent audit and bounded diagnosis of remaining gaps."""
import sys
import control_all_monthly_nav_audit_20261011 as c
c.OUT=c.OUT.parent/'all-monthly-nav-audit-v2'
c.FILES.extend(['audit_monthly_nav_coverage_v2_20261011.py'])
# The base embeds FILES[1:] as executable modules; controller provenance is
# already bound through the final PROBE text included in the protected intent.
c.PROBE=c.PROBE.replace('from audit_monthly_nav_coverage_20261011 import audit','from audit_monthly_nav_coverage_v2_20261011 import audit')
anchor="issue={'subject':row['subject_id'],'period_end':row['period_end'],'issue':str(error)};issues.append(issue)"
replacement="""issue={'subject':row['subject_id'],'period_end':row['period_end'],'issue':str(error)}
            from datetime import date
            start=date.fromisoformat(p['period_end']).replace(day=1).isoformat();series=p['performance']['series'];m=p['monthly_reporting']
            before=[r for r in series if r['date']<start];inside=[r for r in series if start<=r['date']<=p['period_end']];selected=before[-1:]+inside
            class Adapter:
                def execute(self,query,args=()):
                    if not query.lstrip().upper().startswith('SELECT '):raise ValueError('SELECT-only diagnosis')
                    return db.execute(query.replace('?','%s'),args)
            from ibkr_etl.monthly_reporting_analytics import build_monthly_reporting_analytics
            fresh=build_monthly_reporting_analytics(Adapter(),subject_id=p['subject_id'],period_end=p['period_end'],reporting_currency=p['reporting_currency'])
            expected_return=float(selected[-1]['nav'])/float(selected[0]['nav'])-1
            issue.update(frozen_intervals=len(selected)-1,stored_intervals=m.get('daily_observations'),stored_start=m['period_start'],expected_start=selected[1]['date'],stored_end=m['period_end'],expected_end=selected[-1]['date'],stored_return_matches=abs(float(m['portfolio_monthly_return'])-expected_return)<=1e-10,
                fresh_quality=fresh['quality_status'],fresh_intervals=fresh['data'].get('daily_observations'),fresh_return_matches=abs(float(fresh['data']['portfolio_monthly_return'])-expected_return)<=1e-10,
                changed_monthly_fields=[k for k,v in m.items() if fresh['data'].get(k)!=v],fresh_position_coverage=fresh['data'].get('position_coverage'))
            issues.append(issue)"""
if c.PROBE.count(anchor)!=1:raise ValueError('Exact diagnosis anchor required')
c.PROBE=c.PROBE.replace(anchor,replacement)
old_hashes=c.hashes
def hashes():
    values=old_hashes();values['control_all_monthly_nav_audit_v2_20261011.py']=c.hashlib.sha256((c.ROOT/'scripts/control_all_monthly_nav_audit_v2_20261011.py').read_bytes()).hexdigest();return values
c.hashes=hashes
if __name__=='__main__':c.main(*sys.argv[1:])
