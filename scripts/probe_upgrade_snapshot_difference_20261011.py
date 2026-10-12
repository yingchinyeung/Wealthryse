"""Explain snapshot differences using read-only QA/source inputs, no value dumps."""
import json,math,re
from collections import Counter
import qa_read_only as m

def snapshots(database):
    with m.connect(database) as db:
        rows=db.execute("SELECT subject_id,snapshot_payload_json FROM reporting_snapshots WHERE tenant_id=%s AND period_end='2026-09-30' AND status='frozen'",(m.TENANT,)).fetchall()
        return {r['subject_id']:json.loads(r['snapshot_payload_json']) for r in rows}

def changed(a,b,path=''):
    if isinstance(a,(int,float)) and not isinstance(a,bool) and isinstance(b,(int,float)) and not isinstance(b,bool):
        if a==b:return []
        return [(path,'numeric_negligible' if math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-10) else 'numeric_material')]
    if type(a)!=type(b):return [(path,'type_or_presence')]
    if isinstance(a,dict):
        out=[]
        for k in sorted(set(a)|set(b)):
            p=path+'.'+k if path else k
            out += [(p,'type_or_presence')] if k not in a or k not in b else changed(a[k],b[k],p)
        return out
    if isinstance(a,list):
        if len(a)!=len(b):return [(path+'.length','length')]
        return [d for i,(x,y) in enumerate(zip(a,b)) for d in changed(x,y,path+'['+str(i)+']')]
    return [] if a==b else [(path,'text_or_boolean')]

def series_dates(payload):
    rows=(payload.get('performance') or {}).get('series') or []
    values=set()
    for r in rows:
        for key in ('date','snapshot_date','as_of'):
            value=r.get(key)
            if isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):values.add(value);break
    return values

def main():
    old=snapshots(m.SOURCE_DB);new=snapshots(m.TEST_DB)
    if set(old)!=set(new) or len(new)!=5:raise ValueError('Expected five comparable subjects')
    result={}
    for subject in sorted(new):
        a,b=old[subject],new[subject];diff=changed(a,b);material=[p for p,k in diff if k=='numeric_material']
        groups=Counter((re.sub(r'\[\d+\]','[]',p),kind) for p,kind in diff)
        families=[{'path':p,'kind':kind,'count':n} for (p,kind),n in sorted(groups.items())]
        da,db=series_dates(a),series_dates(b)
        comparisons={name:not changed(a.get(name),b.get(name)) for name in ('monthly_reporting','holdings','performance','sector_allocation')}
        result[subject]={'change_counts':dict(Counter(k for _,k in diff)),'families':families[:120],'families_truncated':len(families)>120,
            'material_numeric_paths':material[:60],'material_paths_truncated':len(material)>60,
            'exact_component_equality':comparisons,'performance_dates_added':sorted(db-da),'performance_dates_removed':sorted(da-db),
            'risk_qualification_error_changed':(a.get('risk') or {}).get('crisis_qualification_error')!=(b.get('risk') or {}).get('crisis_qualification_error'),
            'reporting_currency_before':(a.get('portfolio_summary') or {}).get('account',{}).get('reporting_currency'),
            'reporting_currency_after':(b.get('portfolio_summary') or {}).get('account',{}).get('reporting_currency')}
    print('UPGRADE_SNAPSHOT_DIFFERENCES '+json.dumps({'passed':True,'subjects':result,'database_writes':0,'production_connection_used':False,
        'raw_financial_values_retained':False,'tolerance_used_for_classification_only':{'absolute':1e-10,'relative':1e-12},'reports_approved':False}),flush=True)

if __name__=='__main__':main()
