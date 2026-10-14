"""Owner-approved extension for hypothetical U16088571/MS stress only.

Approved 9 October 2026. T retains its separately approved policy. Never used
for actual holdings valuation, NAV, performance or report wording.
"""
from copy import deepcopy
from datetime import date

VERSION = 'us-sleeve-ms-crisis-identity-proxy-v1'
APPROVAL_DATE = '2026-10-09'
SUBJECTS = frozenset(('U16088571', 'alpha-world-ms'))
PARAMOUNT_CONID = '804144296'
PARAMOUNT_SOURCE = 'https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2025-414'
REASON = ('Owner-approved broad-market SPY proxy; policy ' + VERSION +
          ', approved ' + APPROVAL_DATE + '. Current Paramount Skydance shares '
          'started trading on 7 August 2025; predecessor crisis-period security '
          'lineage has not been verified. Hypothetical crisis stress only. '
          'Identity source: ' + PARAMOUNT_SOURCE)


def apply_identity_proxy(mapping, *, subject_id, crisis_start):
    if subject_id not in SUBJECTS:
        raise ValueError('Crisis identity proxy is not approved for this subject')
    start = date.fromisoformat(str(crisis_start))
    result = deepcopy(dict(mapping))
    if str(result.get('conid')) != PARAMOUNT_CONID:
        return result
    if result.get('holding_symbol') not in ('PSKY', 'SKYD'):
        raise ValueError('Paramount broker identity has an unexpected ticker')
    if start >= date(2025, 8, 7):
        return result
    # Idempotent: preserve original evidence on repeated qualification.
    result.update(source_symbol='SPY', mapping_method='market_proxy',
                  proxy_reason=REASON, proxy_policy_version=VERSION,
                  proxy_approval_date=APPROVAL_DATE,
                  proxy_identity_source=PARAMOUNT_SOURCE,
                  original_source_symbol=result.get('original_source_symbol', mapping.get('source_symbol')),
                  original_mapping_method=result.get('original_mapping_method', mapping.get('mapping_method')))
    return result
