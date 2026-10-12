"""Owner-approved T-only security-identity proxy, effective 9 October 2026.

This governs hypothetical crisis replay only, never actual NAV or returns.
The original shared sleeve mapping remains unchanged.
"""
from copy import deepcopy

VERSION='alpha-world-t-crisis-identity-proxy-v1'
APPROVAL_DATE='2026-10-09'
PARAMOUNT_CONID='804144296'
PARAMOUNT_SOURCE='https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2025-414'


def apply_identity_proxy(mapping, *, subject_id, crisis_start):
    if subject_id!='alpha-world-t':
        raise ValueError('T crisis proxy policy cannot alter another subject')
    result=deepcopy(dict(mapping))
    if str(result.get('conid'))!=PARAMOUNT_CONID:
        return result
    if result.get('holding_symbol') not in ('PSKY','SKYD'):
        raise ValueError('Paramount broker identity has an unexpected ticker')
    if crisis_start>='2025-08-07':
        return result
    result.update(
        source_symbol='SPY',mapping_method='market_proxy',
        proxy_reason='Owner-approved broad-market SPY proxy. Current Paramount Skydance shares '
                     'started trading on 7 August 2025; predecessor crisis-period security '
                     'lineage has not been verified. Applies only to hypothetical crisis stress.',
        proxy_policy_version=VERSION,proxy_approval_date=APPROVAL_DATE,
        proxy_identity_source=PARAMOUNT_SOURCE,
        original_source_symbol=mapping.get('source_symbol'),
        original_mapping_method=mapping.get('mapping_method'),
    )
    return result
