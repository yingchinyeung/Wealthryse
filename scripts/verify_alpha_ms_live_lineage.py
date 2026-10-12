"""Independent lineage and ledger arithmetic checks against read-only Azure export."""
from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.risk_snapshots import snapshot_read_status


def main():
    folder = ROOT/'.work/alpha-ms-calculation-audit-20261007'
    data = json.loads((folder/'live-inspection.json').read_bytes())
    old = json.loads((ROOT/'.work/alpha-ms-hkd-hibor-20261007/source-inspection.json').read_bytes())
    counters = Counter()
    failures = []
    uncaptured_source_prices = []

    def check(kind, actual, expected, identity):
        counters[kind] += 1
        if not math.isclose(float(actual),float(expected),rel_tol=1e-10,abs_tol=1e-7):
            failures.append({'check':kind,'identity':identity,'actual':actual,'expected':expected})

    source_nav = {(r['subject_id'],r['snapshot_date'],r['currency']):float(r['total_text']) for r in data['source_nav']}
    prices = {(r['instrument_id'],r['price_date'],r['price_type']):r for r in data['market_prices']}
    components = defaultdict(list)
    for row in data['components']:
        components[row['snapshot_date']].append(row)
        identity = [row['snapshot_date'],row['component_id']]
        if row['component_type'] in ('portfolio','account'):
            expected = source_nav[row['component_id'],row['observation_date'],row['source_currency']]
            check('component_source_nav',row['source_value'],expected,identity)
        elif row['component_type']=='benchmark':
            typ = 'price_index_close' if row['component_id']=='hang-seng-price-index' else 'unadjusted_close'
            price = prices.get((row['component_id'],row['observation_date'],typ))
            if price is None:
                uncaptured_source_prices.append({'identity':identity,'observation_date':row['observation_date']})
            else:
                check('component_source_price',row['source_value'],price['price'],identity)
        check('component_fx_multiplication',row['base_value'],row['source_value']*row['fx_rate_to_base'],identity)
        check('component_contribution',row['contribution'],row['normalized_value']*row['coefficient'],identity)
    for row in data['nav']:
        check('composite_nav_sum',row['total_nav'],sum(c['contribution'] for c in components[row['snapshot_date']]),row['snapshot_date'])
    new_components = {(r['snapshot_date'],r['component_id']):r for r in data['components']}
    for row in old['components']:
        current = new_components[row['snapshot_date'],row['component_id']]
        for field in ('source_value','contribution','fx_rate_to_base','normalized_value'):
            check('captured_component_unchanged',current[field],row[field],[row['snapshot_date'],row['component_id'],field])

    fx_by_pair = defaultdict(list)
    for row in data['broker_fx']:
        if row['account_id']=='U16088606':
            fx_by_pair[row['from_currency'],row['to_currency']].append(row)

    def to_usd(currency, day):
        if currency=='USD': return 1.
        direct = [r for r in fx_by_pair[currency,'USD'] if r['rate_date']<=day]
        if direct: return float(max(direct,key=lambda r:r['rate_date'])['rate_text'])
        inverse = [r for r in fx_by_pair['USD',currency] if r['rate_date']<=day]
        if inverse: return 1/float(max(inverse,key=lambda r:r['rate_date'])['rate_text'])
        raise ValueError('Missing captured FX for '+currency)

    ledger = defaultdict(list)
    opening = {}
    for row in data['hk_ledger']:
        if row['included']:
            if row['source_type']=='opening_balance': opening[row['currency']]=float(row['amount'])
            else: ledger[row['currency']].append(row)
    cash = defaultdict(float)
    for row in data['hk_cash']:
        expected = opening[row['currency']]+sum(float(r['amount']) for r in ledger[row['currency']] if r['event_date']<=row['snapshot_date'])
        check('hk_cash_ledger_balance',row['closing_balance'],expected,[row['snapshot_date'],row['currency']])
        check('hk_cash_open_plus_activity',row['closing_balance'],row['opening_balance']+row['activity'],[row['snapshot_date'],row['currency']])
        check('hk_cash_translation',row['base_value'],row['closing_balance']*to_usd(row['currency'],row['snapshot_date']),[row['snapshot_date'],row['currency']])
        cash[row['snapshot_date']] += row['base_value']
    positions = defaultdict(float)
    for row in data['hk_positions']:
        positions[row['snapshot_date']] += float(row['market_value_text'])*to_usd(row['currency'],row['snapshot_date'])
        check('hk_position_quantity_price',row['market_value_text'],float(row['quantity_text'])*float(row['mark_price_text']),[row['snapshot_date'],row['conid']])
    for row in data['hk_nav']:
        check('hk_nav_cash',row['cash'],cash[row['snapshot_date']],row['snapshot_date'])
        check('hk_nav_balance_sheet',row['total_nav'],sum(float(row.get(k) or 0) for k in ('cash','stock','options','futures','bonds','accruals')),row['snapshot_date'])
        check('hk_nav_positions_plus_cash_accruals',row['total_nav'],positions[row['snapshot_date']]+cash[row['snapshot_date']]+float(row.get('accruals') or 0),row['snapshot_date'])

    interest = [r for r in data['hk_ledger'] if r['included'] and r['source_type']!='opening_balance' and re.search(r'interest|credit\s+int|debit\s+int',r['description'] or '',re.I)]
    cache = next(r for r in data['risk_cache'] if r['as_of_date']=='2026-09-30')
    september = json.loads((ROOT/'.work/alpha-ms-hkd-hibor-20261007/hkd-inputs/2026-09-30.json').read_bytes())
    holding = september['holdings']['holdings']
    nav = september['performance']['series'][-1]['nav']
    report = {
        'captured_at':data['captured_at'],'read_only':True,'checks':dict(counters),'failures':failures,
        'uncaptured_source_prices':uncaptured_source_prices,
        'subjects':data['subjects'],'hibor_rows':len(data['hkd_risk_free']),'benchmark_mappings':data['mappings'],
        'live_september_risk':cache['payload_json'],'live_september_cache_read_status':snapshot_read_status(cache,subject_id='alpha-world-ms',as_of='2026-09-30'),
        'private_september_risk':september['risk']['metrics'],
        'source_transfer_records':len(data['transfers']),
        'cash_description_external_flow_candidates':sum(bool(re.search(r'deposit|withdraw|transfer|contribution|redemption',r['description'] or '',re.I)) for r in data['cash_transactions']),
        'included_derived_external_flow_events':sum(r['included'] and r['external_flow'] for r in data['hk_ledger']),
        'included_actual_interest_events':len(interest),
        'current_hkd_sleeve_exposures':[{ 'component':r['conid'],'nav_units':float(r['market_value_text']),'percent_of_nav':100*float(r['market_value_text'])/nav } for r in holding],
        'current_hkd_gross_risky_exposure_percent':100*sum(abs(float(r['market_value_text'])) for r in holding if r['asset_category']!='cash')/nav,
        'limitations':['Source NAV and market observations are database records; this is not external IBKR or licensed-market-data certification.',
            'Ledger classifications, accrued-income attribution and country exclusions were not independently recertified against every original Flex record.',
            'No source transfers or cash descriptions indicating external flows were found; incomplete upstream capture cannot be ruled out solely by absence.']}
    with (folder/'live-lineage-review.json').open('x',encoding='utf-8') as handle:
        json.dump(report,handle,indent=2,allow_nan=False)
    print(json.dumps({k:v for k,v in report.items() if k not in ('live_september_risk','private_september_risk','benchmark_mappings','failures')},indent=2))
    print('FAILURES',len(failures),json.dumps(failures[:8]))


if __name__=='__main__':main()
