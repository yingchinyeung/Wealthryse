"""Independent equations over frozen source inputs versus actual Azure readers."""
import hashlib
import json
import math
from datetime import date
from pathlib import Path

import run_ms_website_hkd_rehearsal_v6 as rehearsal


def oracle(days,nav,levels):
    p=[nav[b]/nav[a]-1 for a,b in zip(days,days[1:])]
    b=[levels[y]/levels[x]-1 for x,y in zip(days,days[1:])]
    e=[x-y for x,y in zip(p,b)]
    n=len(p)
    def average(values):return math.fsum(values)/len(values)
    def variance(values):
        mean=average(values)
        return math.fsum((x-mean)**2 for x in values)/(len(values)-1)
    def dd(values):
        wealth=[1.0]
        for value in values:wealth.append(wealth[-1]*(1+value))
        return min(value/max(wealth[:i+1])-1 for i,value in enumerate(wealth))
    def capture(positive):
        pairs=[(x,y) for x,y in zip(p,b) if (y>0 if positive else y<0)]
        if not pairs:return None
        pg=math.exp(252/len(pairs)*math.fsum(math.log1p(x) for x,y in pairs))-1
        bg=math.exp(252/len(pairs)*math.fsum(math.log1p(y) for x,y in pairs))-1
        return 100*pg/bg if bg else None
    pt=nav[days[-1]]/nav[days[0]]-1
    bt=levels[days[-1]]/levels[days[0]]-1
    years=(date.fromisoformat(days[-1])-date.fromisoformat(days[0])).days/365.25
    cov=math.fsum((x-average(p))*(y-average(b)) for x,y in zip(p,b))/(n-1)
    return {'period_start':days[0],'period_end':days[-1],'observations':len(days),
        'return_observations':n,'portfolio_total_return':pt,'benchmark_total_return':bt,
        'excess_total_return':pt-bt,'portfolio_annualized_return':(1+pt)**(1/years)-1,
        'benchmark_annualized_return':(1+bt)**(1/years)-1,
        'portfolio_annualized_volatility':math.sqrt(variance(p)*252),
        'benchmark_annualized_volatility':math.sqrt(variance(b)*252),
        'tracking_error':math.sqrt(variance(e)*252),
        'information_ratio':average(e)/math.sqrt(variance(e))*math.sqrt(252) if variance(e) else None,
        'beta':cov/variance(b) if variance(b) else None,
        'correlation':cov/math.sqrt(variance(p)*variance(b)) if variance(p)*variance(b) else None,
        'portfolio_max_drawdown':dd(p),'benchmark_max_drawdown':dd(b),
        'relative_win_rate':sum(x>0 for x in e)/n,
        'upside_capture_pct':capture(True),'downside_capture_pct':capture(False)}


def main():
    stage=rehearsal.STAGE
    qualification=json.loads((stage/'qualified.json').read_bytes())
    rehearsal.probe.verify_protected(qualification)
    proof=json.loads((stage/'proof.json').read_bytes())
    path=rehearsal.probe.FOLDER/'inputs.json'
    if (rehearsal.probe.sha(stage/'proof.json')!=qualification['proof_sha256']
            or rehearsal.probe.sha(path)!=proof['source_binding']['inputs_sha256']):
        raise ValueError('Input/runtime proof integrity differs')
    inputs=json.loads(path.read_bytes())
    if inputs['external_flows']:raise ValueError('Zero-flow oracle cannot ignore external flows')
    nav={r['snapshot_date']:float(r['total_nav']) for r in inputs['nav']}
    fx={r['rate_date']:float(r['rate']) for r in inputs['fx'] if r['from_currency']=='USD' and r['to_currency']=='HKD'}
    audits=[]
    for endpoint in proof['endpoints']:
        identifier=endpoint['benchmark_id']
        levels={r['observation_date']:float(r['level']) for r in inputs['prices']
            if r['benchmark_id']==identifier and r['observation_status']=='provisional_close'}
        if identifier=='GSPC':
            for day in levels:
                fixing=max(d for d in fx if d<=day)
                if (date.fromisoformat(day)-date.fromisoformat(fixing)).days>4:
                    raise ValueError('Stale FX')
                levels[day]*=fx[fixing]
        days=sorted(nav.keys() & levels.keys())
        expected=oracle(days,nav,levels); actual=endpoint['metrics']
        if actual.keys()!=expected.keys():raise ValueError('Metric fields differ')
        for key,value in expected.items():
            observed=actual[key]
            if isinstance(value,(float,int)):
                if observed is None or not math.isclose(observed,value,rel_tol=1e-9,abs_tol=1e-10):
                    raise ValueError('Independent metric mismatch: '+identifier+':'+key)
            elif observed!=value:raise ValueError('Metric convention differs: '+identifier+':'+key)
        audits.append({'benchmark_id':identifier,'metrics_verified':len(expected),
            'start':days[0],'end':days[-1],'observations':len(days)})
    record={'status':'independent_website_metrics_passed','runtime_proof_sha256':qualification['proof_sha256'],
        'input_sha256':proof['source_binding']['inputs_sha256'],'auditor_sha256':rehearsal.probe.sha(Path(__file__)),
        'audits':audits,'persistent_database_writes':0,'factsheets_changed':False}
    receipt=rehearsal.probe.retain(record,'ms-website-hkd-independent-metrics-v1')
    rehearsal.probe.release.build.save(stage,'independent-metrics',{**record,'protected_receipt':receipt})
    print(json.dumps(record),flush=True)


if __name__=='__main__':main()
