"""MS-only versioned risk extension; frozen reports and source NAV stay untouched."""
from copy import deepcopy
from functools import lru_cache
import math,statistics
from .alpha_world_ms_analytics import analyze
from .alpha_world_ms_cache import methodology_identity,validate_payload as validate_core
from .alpha_world_ms_crisis import validate as validate_crisis, IDENTITY_VERSION
from .benchmark_analytics import _simulated_drawdown_distribution,_quantile,_drawdown
from .util import canonical_json

VERSION='alpha-world-ms-hkd-risk-runtime-v2'
UNAVAILABLE={'benchmark_comparison':'separate-HKD-comparison-qualification-required',
             'security_tail_attribution':'not-qualified-for-fixed-unit-composite'}

@lru_cache(maxsize=32)
def extended_metrics(daily):
    if len(daily)<2 or any(isinstance(r,bool) or not math.isfinite(r) or r<=-1 for r in daily):
        raise ValueError('MS requires validated finite daily returns above minus one')
    paths=_simulated_drawdown_distribution(list(daily))
    cutoff=_quantile(paths,.05)
    tail=[r for r in paths if cutoff is not None and r<=cutoff]
    return {'blue_mean_daily_return':statistics.fmean(daily),
        'blue_standard_error':statistics.stdev(daily)/math.sqrt(len(daily)),
        'expected_maximum_drawdown_1y':statistics.fmean(paths) if paths else None,
        'actual_maximum_drawdown_1y':_drawdown(list(daily[-252:])) if len(daily)>=252 else None,
        'tail_maximum_drawdown_1y':statistics.fmean(tail) if tail else None,
        'drawdown_simulation_paths':len(paths),'drawdown_horizon_sessions':252,
        'drawdown_block_length':5,'drawdown_tail_confidence':.95}

def build_payload(inputs):
    source=inputs['ms_hkd_snapshot']
    if (inputs['subject_id']!='alpha-world-ms' or inputs['subject_type']!='composite_portfolio'
        or inputs['as_of']!=source['period_end'] or source.get('methodology_identity')!=methodology_identity()
        or inputs['risk_free']!=source['risk']['risk_free'] or inputs['benchmark_id'] is not None
        or inputs['rows'] or inputs['positions']):raise ValueError('MS runtime input contract differs')
    metrics,daily,evidence=analyze(source)
    if evidence['excluded_intervals']:raise ValueError('MS runtime has unresolved daily return intervals')
    # Preserve the existing currency/calendar/HIBOR validator as an independent
    # core guard. Do not loosen it to admit new metrics or modify its constants.
    from .alpha_world_ms_cache import UNAVAILABLE as CORE_UNAVAILABLE
    core={'account_id':'alpha-world-ms','as_of':inputs['as_of'],'benchmark_id':None,
        'reporting_currency':'HKD','methodology_identity':methodology_identity(),
        'unavailable_capabilities':dict(CORE_UNAVAILABLE),'metrics':metrics,'risk_free':inputs['risk_free'],
        'analytics_evidence':evidence,'expected_tail_loss_contributors':{'top':[],'bottom':[]},'crisis_replay':None}
    validate_core(core)
    crisis=inputs['crisis_replay']
    if crisis is not None:
        if crisis.get('calculation_version') != IDENTITY_VERSION:
            raise ValueError('MS runtime requires the owner-approved crisis identity policy')
        validate_crisis(crisis,as_of=source['data_as_of'],nav=source['performance']['series'][-1]['nav'])
    extra=dict(extended_metrics(tuple(daily)));unavailable=dict(UNAVAILABLE)
    if not extra['drawdown_simulation_paths']:unavailable['one_year_drawdown']='fewer-than-20-validated-return-intervals'
    if crisis is None:unavailable['crisis_replay']='exact-date-qualified-crisis-inputs-unavailable'
    return {**core,'analytics_version':'risk-analytics-v2','runtime_version':VERSION,
        'unavailable_capabilities':unavailable,'source_snapshot':deepcopy(source),
        'metrics':{**metrics,**extra,'daily_expected_tail_loss':metrics['cvar_95_daily'],
            'return_observations':len(daily),'crisis_maximum_drawdown':crisis['maximum_drawdown'] if crisis else None},
        'crisis_replay':deepcopy(crisis),'quality_status':'warn','publication_allowed':False,
        'message':'HKD NAV metrics use inception-to-date average 1M HIBOR. One-year simulations use the documented moving-block bootstrap. Hypothetical crisis results use provisional prices and documented proxies; internal/shareholder analysis only. Security-level tail attribution remains unavailable.'}

def validate_payload(payload):
    if payload.get('runtime_version')!=VERSION:raise ValueError('MS risk runtime version differs')
    inputs={'subject_id':'alpha-world-ms','subject_type':'composite_portfolio','as_of':payload['as_of'],
        'benchmark_id':None,'rows':[],'positions':[],'risk_free':payload['risk_free'],
        'crisis_replay':payload['crisis_replay'],'ms_hkd_snapshot':payload['source_snapshot']}
    if canonical_json(build_payload(inputs))!=canonical_json(payload):raise ValueError('MS runtime arithmetic or evidence differs')
    return True
