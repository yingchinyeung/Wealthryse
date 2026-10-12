"""Read-only identity gate for T's privately reconstructed crisis paths."""
import hashlib,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-onboarding-completion-20261009'
CORPORATE_EVENT={
    'conid':'804144296',
    'symbol':'PSKY',
    'current_security_trading_start':'2025-08-07',
    'issuer_source':'https://www.paramount.com/press/skydance-media-and-paramount-global-complete-merger-creating-next-generation-media-company',
    'exchange_source':'https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2025-414',
    'successor_ticker':'SKYD',
    'successor_ticker_date':'2026-10-06',
    'ticker_source':'https://ir.paramount.com/node/73401/html',
}

def inspect_component(component, crisis_start):
    if (str(component['conid'])==CORPORATE_EVENT['conid']
            and component['source_symbol'] in ('PSKY','SKYD')
            and component['mapping_method']=='direct'
            and crisis_start<CORPORATE_EVENT['current_security_trading_start']):
        return {'conid':str(component['conid']),'source_symbol':component['source_symbol'],
                'issue':'direct_predecessor_history_not_documented',
                'required_resolution':'Verify predecessor lineage or approve a documented market proxy',
                'not_a_finding':'This alone does not prove the vendor price series is numerically wrong'}
    return None

def main():
    issues=[];bindings={}
    for path in sorted((FOLDER/'extended-risk-v2').glob('*.json')):
        raw=path.read_bytes();value=json.loads(raw);bindings[path.name]=hashlib.sha256(raw).hexdigest()
        replay=value['crisis']
        for component in replay.get('components',[]):
            issue=inspect_component(component,replay['daily'][0]['date'])
            if issue:issues.append({'period_end':value['period_end'],**issue})
    result={'status':'blocked_unverified_security_lineage' if issues else 'no_known_identity_conflict',
        'scope':'Targeted corporate-action lineage check, not full historical price certification',
        'corporate_event':CORPORATE_EVENT,'issues':issues,'months_affected':sorted({r['period_end'] for r in issues}),
        'replay_bindings':bindings,'publication_allowed':False,'database_changed':False,
        'actual_nav_changed':False,'proxy_change_applied':False,'owner_decision':'pending'}
    with (FOLDER/'crisis-security-identity-audit.json').open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in ('replay_bindings','corporate_event')}))

if __name__=='__main__':main()
