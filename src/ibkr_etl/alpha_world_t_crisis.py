"""T-only, read-only hypothetical HKD stress, qualified for internal use.

Exact current broker FX values the full Greater China (including Taiwan) and
US sleeves. Archived adjusted USD security/proxy paths are hypothetical; hedge
closes and HKMA FX follow the existing MS convention. No external-price
certification, client-publication permission or extra financing is implied.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext
import json
import math

from .alpha_world_ms_crisis import market_sources, align, require, finite, sha, MARKET_SHA256, database_records
from .alpha_world_t_crisis_policy import apply_identity_proxy, VERSION as PROXY_VERSION
from .subject_onboarding import approved_reference_definitions, configuration_sha256

VERSION = 'alpha-world-t-hkd-crisis-replay-v1'
SUBJECT = 'alpha-world-t'
SLEEVES = ('U16088606', 'U16088571')
COMPONENTS = frozenset((*SLEEVES, 'hang-seng-price-index', 'spy-settlement', 'constant-cash'))


def decimal(value, *, positive=False):
    finite(value, positive=positive)
    return Decimal(str(value))


def unique(rows, key, label):
    result = {key(row): row for row in rows}
    require(len(result) == len(rows), 'Duplicate T crisis ' + label)
    return result


def _sleeve(records, account, day, days):
    navs = [r for r in records['source_nav'] if r['subject_id'] == account
            and str(r['snapshot_date']) == day and r['currency'] == 'USD']
    require(len(navs) == 1, 'T crisis requires exact unique source USD NAV')
    nav = decimal(navs[0]['total_text'], positive=True)
    held = unique([r for r in records['source_positions'] if r['subject_id'] == account
                   and str(r['snapshot_date']) == day], lambda r: str(r['conid']), 'holding')
    require(bool(held), 'T crisis source holdings missing')
    original_maps = [r for r in records['mappings'] if r['subject_id'] == account
                     and str(r['holdings_as_of']) == day]
    maps = unique(original_maps, lambda r: str(r['conid']), 'mapping')
    residual_receivables = []
    for conid in sorted(set(held) - set(maps)):
        row = held[conid]
        attrs = row.get('broker_attributes')
        attrs = json.loads(attrs) if isinstance(attrs, str) else attrs
        require(isinstance(attrs, dict) and attrs.get('symbol', '').endswith('.REC')
                and attrs.get('listingExchange') == 'CORPACT'
                and str(attrs.get('conid')) == conid
                and attrs.get('accountId') == account
                and str(attrs.get('reportDate')) == day.replace('-', '')
                and attrs.get('currency') == row['currency']
                and decimal(attrs['positionValue']) == decimal(row['market_value_text']),
                'T crisis unmapped holding is not a verified original broker receivable')
        residual_receivables.append({'conid':conid, 'symbol':attrs['symbol'],
            'value':str(row['market_value_text']), 'currency':row['currency'],
            'broker_evidence_sha256':sha(attrs), 'treatment':'included in flat source NAV residual'})
        del held[conid]
    require(set(held) == set(maps), 'T crisis exact holdings/mapping coverage differs')
    fx = unique([r for r in records['broker_fx'] if r['account_id'] == account
                 and str(r['rate_date']) == day and r['to_currency'] == 'USD'],
                lambda r: r['from_currency'], 'broker FX')
    prices = unique([r for r in records['prices'] if str(r['price_date']) in days],
                    lambda r: (r['symbol'], str(r['price_date'])), 'security price')
    entries = []
    for conid in sorted(maps):
        row = held[conid]
        mapping = apply_identity_proxy(maps[conid], subject_id=SUBJECT, crisis_start=days[0])
        require(mapping['currency'] == 'USD' and mapping['price_type'] == 'adjusted_close'
                and mapping['mapping_method'] in ('direct', 'market_proxy'), 'T crisis mapping convention differs')
        require(mapping['mapping_method'] != 'market_proxy' or bool(mapping.get('proxy_reason')),
                'T crisis proxy reason missing')
        rate = Decimal(1) if row['currency'] == 'USD' else decimal(fx[row['currency']]['rate_text'], positive=True)
        value = decimal(row['market_value_text']) * rate
        recorded = decimal(mapping['current_market_value'])
        require(abs(decimal(mapping['opening_weight']) - recorded/nav) <= Decimal('1e-9'),
                'T legacy source weight is internally inconsistent')
        series = []
        for day_ in days:
            price = prices[mapping['source_symbol'], day_]
            require(price['currency'] == 'USD' and price['price_type'] == 'adjusted_close',
                    'T crisis security price convention differs')
            series.append(decimal(price['price'], positive=True))
        entries.append({'conid':conid, 'source_symbol':mapping['source_symbol'],
            'mapping_method':mapping['mapping_method'], 'proxy_reason':mapping.get('proxy_reason'),
            'mapping_evidence':mapping, 'current_value_usd':str(value), 'broker_fx_to_usd':str(rate),
            'holding_currency':row['currency'], 'legacy_value_usd':str(recorded),
            'adjusted_prices':[str(v) for v in series]})
    cash = nav - sum(decimal(r['current_value_usd']) for r in entries)
    levels = []
    for i in range(len(days)):
        level = (cash + sum(decimal(r['current_value_usd']) * decimal(r['adjusted_prices'][i])
                            / decimal(r['adjusted_prices'][0]) for r in entries)) / nav
        independent = (float(cash) + sum(float(r['current_value_usd']) * float(r['adjusted_prices'][i])
                                        / float(r['adjusted_prices'][0]) for r in entries)) / float(nav)
        require(level > 0 and math.isclose(float(level), independent, abs_tol=2e-12, rel_tol=0),
                'T sleeve independent arithmetic differs')
        levels.append(level)
    return levels, {'account':account, 'nav_usd':str(nav), 'flat_residual_usd':str(cash),
                    'components':entries, 'receivables':residual_receivables}


def compute(inputs):
    require(inputs['subject_id'] == SUBJECT and inputs['reporting_currency'] == 'HKD', 'T HKD stress required')
    require(inputs['contract_sha256'] == configuration_sha256(approved_reference_definitions()[SUBJECT]),
            'T crisis calculation contract differs')
    day = str(inputs['holdings_as_of'])
    require(date(2025, 1, 20) <= date.fromisoformat(day) <= date.fromisoformat(inputs['requested_as_of']),
            'T crisis holdings date invalid')
    values = inputs['component_values']
    require(set(values) == COMPONENTS, 'T crisis composite component identities differ')
    values = {k:decimal(v) for k,v in values.items()}
    nav = decimal(inputs['nav'], positive=True)
    require(abs(sum(values.values())-nav) <= Decimal('1e-9') and values['constant-cash'] == 58
            and all(values[k] > 0 for k in SLEEVES)
            and values['hang-seng-price-index'] < 0 and values['spy-settlement'] < 0,
            'T crisis signed components do not reconcile to NAV')
    market = market_sources(); days = market['calendar']
    fx, fx_audit = align(market['fx']['observations'], days, 'rate')
    hsi, hsi_audit = align(market['hedges']['hsi']['observations'], days, 'price', hong_kong_sessions=True)
    spy, spy_audit = align(market['hedges']['spy']['observations'], days, 'price')
    sleeves, evidence = {}, {}
    rows = []; peak = Decimal(1)
    with localcontext() as context:
        context.prec = 40
        for account in SLEEVES:
            sleeves[account], evidence[account] = _sleeve(inputs['records'], account, day, days)
        for i, day_ in enumerate(days):
            currency_factor = decimal(fx[day_])/decimal(fx[days[0]])
            level = (values['constant-cash'] + sum(values[a]*sleeves[a][i]*currency_factor for a in SLEEVES)
                     + values['hang-seng-price-index']*decimal(hsi[day_])/decimal(hsi[days[0]])
                     + values['spy-settlement']*decimal(spy[day_])/decimal(spy[days[0]])*currency_factor)/nav
            independent = (float(values['constant-cash'])
                + sum(float(values[a])*float(sleeves[a][i])*float(currency_factor) for a in SLEEVES)
                + float(values['hang-seng-price-index'])*hsi[day_]/hsi[days[0]]
                + float(values['spy-settlement'])*spy[day_]/spy[days[0]]*float(currency_factor))/float(nav)
            require(level > 0 and math.isclose(float(level), independent, abs_tol=2e-12, rel_tol=0),
                    'T composite independent arithmetic differs')
            peak = max(peak, level)
            rows.append({'observation_date':day_, 'normalized_value':float(level), 'drawdown':float(level/peak-1)})
    require(math.isclose(rows[0]['normalized_value'], 1., abs_tol=1e-10, rel_tol=0), 'T stress opening differs')
    return {'subject_id':SUBJECT, 'reporting_currency':'HKD', 'calculation_version':VERSION,
        'requested_as_of':inputs['requested_as_of'], 'holdings_as_of':day,
        'crisis_start':days[0], 'crisis_end':days[-1], 'observations':len(days),
        'maximum_drawdown':min(r['drawdown'] for r in rows), 'daily':rows,
        'quality_status':'warn', 'qualification_status':'qualified_internal',
        'independent_decimal_verified':True, 'provider_prices_independently_verified':False,
        'publication_status':'blocked_provisional_market_data', 'proxy_policy_version':PROXY_VERSION,
        'market_source_sha256':MARKET_SHA256, 'input_sha256':sha(inputs), 'inputs':deepcopy(inputs),
        'sleeve_evidence':evidence, 'fx_and_hedge_alignment_sha256':sha([fx_audit,hsi_audit,spy_audit]),
        'proxy_components':sum(r['mapping_method']=='market_proxy' for e in evidence.values() for r in e['components']),
        'direct_components':2+sum(r['mapping_method']=='direct' for e in evidence.values() for r in e['components']),
        'cash_interest':'none', 'projected_financing':'none',
        'limitations':'Hypothetical current-holdings replay, not actual 2008–2009 T performance. Archived provisional prices and documented proxies; exact current broker FX; constant HKD cash; no projected financing or trading costs. Internal/shareholder use only.'}


def validate(replay, *, as_of=None, holdings=None, nav=None):
    require(isinstance(replay, dict) and replay.get('calculation_version') == VERSION,
            'Qualified T HKD crisis replay required')
    require(compute(replay['inputs']) == replay, 'T crisis result differs from deterministic verification')
    if as_of is not None:
        require(replay['holdings_as_of'] == as_of, 'T crisis holdings date differs')
    if holdings is not None:
        require(len(holdings) == len(COMPONENTS) and all(r['currency']=='HKD' for r in holdings)
                and {r['conid']:float(r['market_value_text']) for r in holdings}
                    == {k:float(v) for k,v in replay['inputs']['component_values'].items()}, 'T crisis holdings differ')
    if nav is not None:
        require(decimal(nav) == decimal(replay['inputs']['nav']), 'T crisis NAV differs')
    return replay


def from_capture(records, snapshot):
    require(snapshot['subject_id'] == SUBJECT and snapshot['reporting_currency'] == 'HKD', 'T HKD snapshot required')
    holdings = snapshot['holdings']['holdings']; day = snapshot['data_as_of']
    require(len(holdings) == len(COMPONENTS) and all(r['currency']=='HKD' for r in holdings), 'T holdings invalid')
    scoped = {'source_nav':[r for r in records['source_nav'] if str(r['snapshot_date'])==day],
        'source_positions':[r for r in records['source_positions'] if str(r['snapshot_date'])==day],
        'mappings':[r for r in records['mappings'] if str(r['holdings_as_of'])==day],
        'broker_fx':[r for r in records['broker_fx'] if str(r['rate_date'])==day], 'prices':records['prices']}
    result = compute({'subject_id':SUBJECT, 'reporting_currency':'HKD', 'requested_as_of':snapshot['period_end'],
        'holdings_as_of':day, 'contract_sha256':configuration_sha256(approved_reference_definitions()[SUBJECT]),
        'nav':snapshot['portfolio_summary']['latest_nav']['total_text'],
        'component_values':{r['conid']:r['market_value_text'] for r in holdings}, 'records':scoped})
    return validate(result, as_of=day, holdings=holdings, nav=snapshot['portfolio_summary']['latest_nav']['total_text'])


def qualified_snapshot(snapshot, replay):
    require(snapshot['subject_id']==SUBJECT and snapshot.get('publication_allowed') is False,
            'Private T snapshot required')
    validate(replay, as_of=snapshot['data_as_of'], holdings=snapshot['holdings']['holdings'],
             nav=snapshot['portfolio_summary']['latest_nav']['total_text'])
    result = deepcopy(snapshot)
    result['risk']['crisis_replay'] = deepcopy(replay)
    result['publication_blockers'] = [b for b in result.get('publication_blockers', []) if b != 'risk:crisis_replay_unavailable']
    require(bool(result['publication_blockers']), 'T provisional publication restrictions must remain')
    result['quality_status'] = 'warn'
    result['snapshot_version'] = str(result.get('snapshot_version', 'private-t')) + '+' + VERSION
    return result


def capture_records(connection, *, tenant_id, holdings_as_of):
    """Read exact dated broker evidence; no refreshes, writes or shared-map edits."""
    require(bool(tenant_id), 'T crisis requires an explicit tenant')
    date.fromisoformat(holdings_as_of)
    args=(tenant_id, *SLEEVES, holdings_as_of)
    nav=database_records(connection.execute('''SELECT subject_id,snapshot_date,currency,total_text
        FROM subject_nav_series WHERE tenant_id=? AND subject_id IN (?,?)
        AND snapshot_date=? AND currency='USD' ORDER BY subject_id''', args).fetchall())
    positions=database_records(connection.execute('''SELECT p.*,p.account_id AS subject_id
        FROM position_snapshots p JOIN accounts a ON a.account_id=p.account_id
        WHERE a.tenant_id=? AND p.account_id IN (?,?) AND p.snapshot_date=?
        ORDER BY p.account_id,p.conid,p.model_code''', args).fetchall())
    mappings=database_records(connection.execute('''SELECT * FROM crisis_replay_components
        WHERE tenant_id=? AND subject_id IN (?,?) AND holdings_as_of=?
        ORDER BY subject_id,conid''', args).fetchall())
    broker_fx=database_records(connection.execute('''SELECT f.* FROM fx_rates f
        JOIN accounts a ON a.account_id=f.account_id
        WHERE a.tenant_id=? AND f.account_id IN (?,?) AND f.rate_date=?
        AND f.to_currency='USD' ORDER BY f.account_id,f.from_currency''', args).fetchall())
    keys={(r['subject_id'],str(r['conid'])) for r in mappings}
    for position in positions:
        if (position['subject_id'],str(position['conid'])) in keys:
            continue
        originals=connection.execute('''SELECT DISTINCT data_json FROM flex_rows
            WHERE tenant_id=? AND account_id=? AND row_sha256=? AND record_type='OpenPosition' ''',
            (tenant_id,position['subject_id'],position['row_sha256'])).fetchall()
        attrs=[json.loads(r['data_json']) if isinstance(r['data_json'],str) else dict(r['data_json']) for r in originals]
        require(bool(attrs) and all(a==attrs[0] for a in attrs),
                f"T receivable original evidence missing or ambiguous: {position['subject_id']} {holdings_as_of} {position['conid']}")
        position['broker_attributes']=attrs[0]
    prices=database_records(connection.execute('''SELECT * FROM market_data_prices
        WHERE tenant_id=? AND instrument_id LIKE ? AND price_date BETWEEN ? AND ?
        ORDER BY symbol,price_date''', (tenant_id,'crisis-replay-%','2008-09-15','2009-03-09')).fetchall())
    return {'source_nav':nav,'source_positions':positions,'mappings':mappings,'broker_fx':broker_fx,'prices':prices}


def load(connection, snapshot, *, tenant_id):
    """Production candidate producer: requires the approved T contract first."""
    from .alpha_world_t_analytics import load_risk_snapshot
    current,_,_=load_risk_snapshot(connection,snapshot['period_end'],tenant_id=tenant_id)
    require(current['data_as_of']==snapshot['data_as_of'], 'T crisis snapshot date drifted')
    latest=current['performance']['series'][-1]['nav']
    result_snapshot=deepcopy(snapshot)
    if 'holdings' not in result_snapshot:
        rows=connection.execute('''SELECT component_id,contribution,base_currency
            FROM synthetic_portfolio_components WHERE tenant_id=? AND portfolio_id=?
            AND snapshot_date=? ORDER BY component_id''', (tenant_id,SUBJECT,current['data_as_of'])).fetchall()
        result_snapshot['holdings']={'holdings':[{'conid':r['component_id'],
            'market_value_text':str(r['contribution']),'currency':r['base_currency']} for r in rows]}
        result_snapshot['portfolio_summary']={'latest_nav':{'total_text':str(latest)}}
    require(math.isclose(float(result_snapshot['portfolio_summary']['latest_nav']['total_text']),latest,
                        rel_tol=0,abs_tol=1e-10),'T crisis current NAV differs from snapshot')
    return from_capture(capture_records(connection,tenant_id=tenant_id,
                                       holdings_as_of=current['data_as_of']),result_snapshot)
