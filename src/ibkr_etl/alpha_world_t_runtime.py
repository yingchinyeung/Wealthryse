"""Qualified T HKD risk candidate. No market fetch, approvals or activation."""
from copy import deepcopy
from .alpha_world_t_cache import _qualified_input,canonical
from .alpha_world_t_analytics import methodology_identity
from .alpha_world_t_crisis import validate as validate_crisis
from .alpha_world_t_presentation import extended_metrics

VERSION='alpha-world-t-hkd-risk-runtime-v1'
UNAVAILABLE={'benchmark_comparison':'separate-HKD-comparison-qualification-required',
             'security_tail_attribution':'not-qualified-for-fixed-unit-composite'}

def validate_inputs(inputs):
    core=inputs['t_hkd_snapshot'];source,_,_=_qualified_input(core)
    if (inputs['subject_id']!='alpha-world-t' or inputs['subject_type']!='composite_portfolio'
        or inputs['as_of']!=source['period_end'] or inputs['risk_free']!=source['risk']['risk_free']
        or inputs['benchmark_id'] is not None or inputs['rows'] or inputs['positions']):
        raise ValueError('T runtime input identity or methodology differs')
    validate_crisis(inputs['crisis_replay'],as_of=source['data_as_of'],nav=source['performance']['series'][-1]['nav'])
    return source

def build_payload(inputs):
    from .alpha_world_t_analytics import analyze
    source=validate_inputs(inputs);metrics,daily,evidence=analyze(source)
    if evidence['excluded_intervals']:raise ValueError('T runtime daily sample has unresolved gaps')
    crisis=inputs['crisis_replay']
    return {'account_id':'alpha-world-t','as_of':inputs['as_of'],'benchmark_id':None,
        'analytics_version':'risk-analytics-v2','runtime_version':VERSION,
        'methodology_identity':methodology_identity(),'unavailable_capabilities':dict(UNAVAILABLE),
        'reporting_currency':'HKD','quality_status':'warn','publication_allowed':False,
        'metrics':{**metrics,**extended_metrics(daily),'daily_expected_tail_loss':metrics['cvar_95_daily'],
            'return_observations':len(daily),'crisis_maximum_drawdown':crisis['maximum_drawdown']},
        'risk_free':deepcopy(source['risk']['risk_free']), 'crisis_replay':deepcopy(crisis),
        'expected_tail_loss_contributors':{'top':[],'bottom':[]},
        'source_snapshot':source,'analytics_evidence':evidence,
        'message':'HKD NAV metrics use inception-to-date average 1M HIBOR. Hypothetical crisis uses provisional prices and documented proxies; internal/shareholder use only. Benchmark comparison and security-level tail attribution remain unavailable.'}

def validate_payload(payload):
    if payload.get('runtime_version')!=VERSION:raise ValueError('T runtime version differs')
    source=payload['source_snapshot']
    inputs={'subject_id':'alpha-world-t','subject_type':'composite_portfolio','as_of':payload['as_of'],
        'benchmark_id':None,'rows':[],'positions':[],'t_hkd_snapshot':source,
        'risk_free':payload['risk_free'],'crisis_replay':payload['crisis_replay']}
    if canonical(build_payload(inputs))!=canonical(payload):raise ValueError('T cached runtime arithmetic or evidence differs')
    return True
