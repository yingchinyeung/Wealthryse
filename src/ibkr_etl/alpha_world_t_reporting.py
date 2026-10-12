"""T-only reporting inputs from approved HKD history and fixed-unit components."""
from copy import deepcopy
from collections import defaultdict
import math
from .alpha_world_t_analytics import analyze
from .alpha_world_t_crisis import COMPONENTS,validate as validate_crisis
from .alpha_world_t_cache import _qualified_input

VERSION='alpha-world-t-reporting-inputs-v1'
NAMES={'U16088606':('Greater China','Greater China including Taiwan','Long account NAV'),
 'U16088571':('US Tech','US Tech & Biotech','Long account NAV'),
 'hang-seng-price-index':('HSI','Hang Seng Index','Short price index'),
 'spy-settlement':('SPY','S&P 500 via SPY','Short price index'),
 'constant-cash':('Cash','Constant cash','Constant cash')}
COEFFICIENTS={'U16088606':.91,'U16088571':.45,'hang-seng-price-index':-.49,'spy-settlement':-.45,'constant-cash':58.}

def require(ok,message):
    if not ok:raise ValueError(message)

def enrich(core,components,crisis=None):
    """Validate every dated component and derive monthly reporting; never mutate inputs."""
    _,metrics,evidence=_qualified_input(core)
    snapshot=deepcopy(core);series=snapshot['performance']['series']
    by_day=defaultdict(dict)
    for raw in components:
        row=dict(raw);day=str(row['snapshot_date']);key=str(row['component_id'])
        require(row['portfolio_id']=='alpha-world-t' and row['base_currency']=='HKD'
                and key in COMPONENTS and key not in by_day[day],'T component identity/currency duplicated or incorrect')
        fields={k:float(row[k]) for k in ('coefficient','source_value','fx_rate_to_base','base_value','normalized_value','contribution')}
        require(all(math.isfinite(v) for v in fields.values()) and fields['fx_rate_to_base']>0,'T component valuation invalid')
        require(fields['coefficient']==COEFFICIENTS[key],'T fixed-unit coefficient differs from approved composition')
        require(math.isclose(fields['source_value']*fields['fx_rate_to_base'],fields['base_value'],abs_tol=1e-8,rel_tol=1e-12)
                and math.isclose(fields['coefficient']*fields['normalized_value'],fields['contribution'],abs_tol=1e-10,rel_tol=1e-12),
                'T component valuation identity differs')
        expected_direction='cash' if key=='constant-cash' else 'short' if key in ('hang-seng-price-index','spy-settlement') else 'long'
        require(row['direction']==expected_direction,'T component direction differs')
        require((key=='constant-cash' and fields['contribution']==58)
                or (expected_direction=='long' and fields['contribution']>0)
                or (expected_direction=='short' and fields['contribution']<0),'T component sign or constant cash differs')
        by_day[day][key]=row
    require(set(by_day)=={r['date'] for r in series},'T component history dates differ from NAV history')
    for point in series:
        rows=by_day[point['date']]
        require(set(rows)==COMPONENTS and math.isclose(sum(float(r['contribution']) for r in rows.values()),float(point['nav']),
                abs_tol=1e-9,rel_tol=0),'T dated components do not reconcile to NAV')
    months={}
    for row in series:months[row['date'][:7]]=row
    monthly_rows=[];previous=float(series[0]['nav'])
    for row in months.values():
        value=float(row['nav'])
        monthly_rows.append({'date':row['date'],'portfolio':value,'portfolio_monthly_return':value/previous-1,'benchmark_monthly_return':None})
        previous=value
    start=monthly_rows[-2]['date'] if len(monthly_rows)>1 else series[0]['date']
    start_nav=monthly_rows[-2]['portfolio'] if len(monthly_rows)>1 else float(series[0]['nav'])
    before,after=by_day[start],by_day[core['data_as_of']]
    holdings=[];contributions=[];sector=defaultdict(float);sector_effect=defaultdict(float)
    for key,(symbol,name,category) in NAMES.items():
        row=after[key];value=float(row['contribution']);effect=(value-float(before[key]['contribution']))/start_nav
        holdings.append({'conid':key,'symbol':symbol,'description':name,'currency':'HKD','market_value_text':str(value),
            'quantity_text':None,'mark_price_text':None,'holding_kind':'synthetic_exposure',
            'asset_category':'cash' if key=='constant-cash' else 'component',
            'valuation_basis':'coefficient-times-normalized-source','quantity_basis':'not-applicable-no-security-units'})
        contributions.append({'conid':key,'symbol':symbol,'description':name,'contribution':effect})
        sector[category]+=abs(value);sector_effect[category]+=effect
    gross=sum(sector.values())
    require(math.isclose(sum(r['contribution'] for r in contributions),monthly_rows[-1]['portfolio_monthly_return'],
                        abs_tol=1e-10,rel_tol=0),'T monthly contribution bridge differs')
    snapshot.update(snapshot_version=VERSION,formula_version=evidence['version'],quality_status='warn',
        publication_allowed=False,publication_blockers=['primary_benchmark:not_licensed_for_publication','secondary_1_benchmark:not_licensed_for_publication'],
        month_end_performance=monthly_rows,
        portfolio_summary={'account':{'account_id':'alpha-world-t','alias':'Alpha World T','base_currency':'HKD'},
            'latest_nav':{'snapshot_date':core['data_as_of'],'currency':'HKD','total_text':str(series[-1]['nav'])}},
        holdings={'holdings':holdings},sector_allocation={'available':True,'publication_allowed':True,
            'classification_basis':'composite sleeve type','classification_coverage_pct':100,'gross_market_value':gross,
            'allocation':[{'sector':k,'weight':v/gross,'market_value':v} for k,v in sector.items()]},
        monthly_reporting={'available':True,'period_start':start,'period_end':core['data_as_of'],
            'reporting_currency':'HKD','method_version':'fixed-unit-component-delta-over-prior-month-nav-v1',
            'portfolio_monthly_return':monthly_rows[-1]['portfolio_monthly_return'],'security_contributions':contributions,
            'sector_contributions':[{'sector':k,'contribution':v} for k,v in sector_effect.items()],
            'equity_contribution':sum(r['contribution'] for r in contributions),'residual_contribution':0,
            'cash_activity':{'dividends_by_symbol':[]}},
        performance_comparison={'available':False,'series':[],'metrics':{},'publication_allowed':False},
        secondary_performance_comparison={'available':False,'series':[],'metrics':{},'publication_allowed':False})
    snapshot['risk'].update(metrics=metrics,analytics_evidence=evidence,crisis_replay=deepcopy(crisis))
    if crisis:
        validate_crisis(crisis,as_of=core['data_as_of'],holdings=holdings,nav=snapshot['portfolio_summary']['latest_nav']['total_text'])
    else:snapshot['publication_blockers'].append('risk:crisis_replay_unavailable')
    return snapshot

def load(connection,as_of,*,tenant_id):
    from .alpha_world_t_analytics import load_risk_snapshot
    from .alpha_world_t_crisis import capture_records,from_capture
    core,_,_=load_risk_snapshot(connection,as_of,tenant_id=tenant_id)
    components=connection.execute('''SELECT * FROM synthetic_portfolio_components
        WHERE tenant_id=? AND portfolio_id='alpha-world-t' AND snapshot_date<=?
        ORDER BY snapshot_date,component_id''',(tenant_id,core['data_as_of'])).fetchall()
    snapshot=enrich(core,components)
    replay=from_capture(capture_records(connection,tenant_id=tenant_id,holdings_as_of=core['data_as_of']),snapshot)
    return enrich(core,components,replay)
