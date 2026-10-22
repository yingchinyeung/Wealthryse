"""Qualified, read-only HKD stress replay of the exact MS as-of composition.

Reconcile existing USD sleeve replays independently, then translate each sleeve
to HKD BEFORE combining signed MS component values. Hedges use unadjusted closes.
This hypothetical stress test adds no financing charge and earns no cash interest.
"""
from datetime import date, timedelta
from decimal import Decimal, localcontext
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from uuid import UUID

from .subject_onboarding import approved_reference_definitions, configuration_sha256
from .util import canonical_json
from .crisis_identity_policy import apply_identity_proxy, VERSION as IDENTITY_PROXY_VERSION

VERSION = 'alpha-world-ms-hkd-crisis-replay-v1'
IDENTITY_VERSION = 'alpha-world-ms-hkd-crisis-replay-v2'
MARKET_SHA256 = '422df9135669fe1478f0d18544d1a55926e612859659ea72b151c73a496d81e2'
SOURCE_VERSION = 'as-of-holdings-crisis-replay-v3'
SLEEVE_VERSION = 'independent-recorded-price-sleeve-replay-v1'
SLEEVES = ('hong-kong-china', 'U16088571')
COMPONENTS = frozenset((*SLEEVES, 'hang-seng-price-index', 'spy-settlement', 'constant-cash'))
# Explicit crisis-period Hong Kong closures, never inferred from missing prices.
# https://www.info.gov.hk/gia/general/200704/13/P200704130119.htm
# https://www.info.gov.hk/gia/general/200804/11/P200804110164.htm
# HKEX securities trading rule: weekdays except Hong Kong public holidays.
HK_CLOSED = frozenset(('2008-09-15','2008-10-01','2008-10-07','2008-12-25',
    '2008-12-26','2009-01-01','2009-01-26','2009-01-27','2009-01-28'))


def require(value, message):
    if not value:raise ValueError(message)


def sha(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def database_records(result):
    """Normalize typed PostgreSQL identifiers without coercing financial values."""
    return json.loads(canonical_json([{key:str(value) if isinstance(value,UUID) else value
                                      for key,value in dict(row).items()} for row in result]))


def finite(value, *, positive=False):
    require(not isinstance(value, bool), 'Boolean is not a financial number')
    number = float(value)
    require(math.isfinite(number) and (not positive or number > 0), 'Invalid financial number')
    return number


@lru_cache(maxsize=1)
def market_sources():
    data = Path(__file__).with_name('assets').joinpath('alpha_world_ms_crisis_market_v1.json').read_bytes()
    require(hashlib.sha256(data).hexdigest() == MARKET_SHA256, 'Archived crisis market source differs')
    value = json.loads(data)
    require(value['version'] == 'ms-crisis-market-sources-v1', 'Crisis market source version differs')
    days = value['calendar']
    require(days == sorted(set(days)) and len(days) == 121 and days[0] == '2008-09-15'
            and days[-1] == '2009-03-09', 'Incomplete crisis calendar')
    require(value['fx']['from_currency'] == 'USD' and value['fx']['to_currency'] == 'HKD'
            and value['fx']['provider'] == 'HKMA' and value['fx']['unit'] == 'HKD per USD', 'FX direction differs')
    for label, currency in (('spy', 'USD'), ('hsi', 'HKD')):
        require(value['hedges'][label]['currency'] == currency
                and value['hedges'][label]['price_type'] == 'unadjusted_close', 'Wrong hedge price convention')
    return value


def align(observations, calendar, field, *, hong_kong_sessions=False):
    rows = {str(r['date']): finite(r[field], positive=True) for r in observations}
    require(len(rows) == len(observations), 'Duplicate historical source date')
    ordered = sorted(rows)
    output, audit = {}, []
    for day in calendar:
        prior = [d for d in ordered if d <= day]
        require(bool(prior), 'Historical source has no non-forward-looking opening value')
        observed = prior[-1]
        age = (date.fromisoformat(day) - date.fromisoformat(observed)).days
        if hong_kong_sessions:
            intervening = [date.fromisoformat(observed)+timedelta(days=n) for n in range(1,age+1)]
            require(all(d.weekday()>=5 or d.isoformat() in HK_CLOSED for d in intervening),
                    'Missing historical Hong Kong trading-session observation')
        else:require(age <= 4, 'Historical crisis source exceeds four-day carry')
        output[day] = rows[observed]
        audit.append({'date': day, 'source_date': observed, 'value': rows[observed]})
    return output, audit


def verify_sleeve(records, subject, holdings_day, calendar, *, identity_proxy=False):
    """Verify each stored sleeve against its exact holdings, FX, prices and NAV."""
    def scoped(name, day_field):
        return [r for r in records[name] if r['subject_id'] == subject and str(r[day_field]) == holdings_day]
    navs = [r for r in scoped('source_nav', 'snapshot_date') if r['currency'] == 'USD']
    require(len(navs) == 1, 'Unique USD sleeve NAV required')
    nav = finite(navs[0]['total_text'], positive=True)
    held = scoped('source_positions', 'snapshot_date')
    holdings = {str(r['conid']): r for r in held}
    maps = scoped('mappings', 'holdings_as_of')
    original_maps = maps
    if identity_proxy and subject == 'U16088571':
        maps = [apply_identity_proxy(r, subject_id='alpha-world-ms', crisis_start=calendar[0]) for r in maps]
    require(len(holdings) == len(held) == len(maps) and set(holdings) == {str(r['conid']) for r in maps},
            'Exact sleeve holdings/mapping identities differ')
    stored = scoped('replays', 'holdings_as_of')
    require(len(stored) == len(calendar) and [str(r['observation_date']) for r in stored] == calendar
            and all(r['calculation_version'] == SOURCE_VERSION and r['quality_status'] in ('pass','warn') for r in stored),
            'Qualified same-date source replay is unavailable')
    symbols = {r['source_symbol'] for r in maps}
    prices = {}
    used_prices = [r for r in records['prices'] if r['symbol'] in symbols and str(r['price_date']) in calendar]
    for row in used_prices:
        require(row['currency'] == 'USD' and row['price_type'] == 'adjusted_close', 'Source replay prices use another currency/convention')
        key = (row['symbol'], str(row['price_date']))
        require(key not in prices, 'Ambiguous source replay price')
        prices[key] = finite(row['price'], positive=True)
    consumed_fx = []
    for mapping in maps:
        original = holdings[str(mapping['conid'])]
        value = finite(original['market_value_text'])
        converted = finite(mapping['current_market_value'])
        require(mapping['currency'] == 'USD' and mapping['price_type'] == 'adjusted_close'
                and mapping['mapping_method'] in ('direct','market_proxy'), 'Unqualified source mapping')
        if mapping['mapping_method'] == 'market_proxy':
            require(bool(mapping.get('proxy_reason')), 'Undocumented source proxy')
        require(math.isclose(finite(mapping['opening_weight']), converted/nav, rel_tol=1e-10, abs_tol=1e-12),
                'Source opening weight differs from NAV')
        if original['currency'] == 'USD':
            require(math.isclose(converted, value, rel_tol=1e-10, abs_tol=1e-8), 'USD source holding differs')
        else:
            require(original['currency'] == 'HKD', 'Unsupported source holding currency')
            rates = [r for r in records['source_fx'] if r['from_currency'] == 'HKD' and r['to_currency'] == 'USD'
                     and r['quality_status'] in ('pass','warn')
                     and 0 <= (date.fromisoformat(holdings_day)-date.fromisoformat(str(r['rate_date']))).days <= 4]
            matching = [r for r in rates if math.isclose(converted, value*finite(r['rate'],positive=True), rel_tol=1e-10, abs_tol=1e-8)]
            require(bool(matching),
                    'Recorded sleeve FX does not reconcile to the exact source holding')
            for row in matching:
                if row not in consumed_fx:consumed_fx.append(row)
        require(all((mapping['source_symbol'], day) in prices for day in calendar), 'Incomplete source replay prices')
    # Rebuild from archived prices, not mutable/previously rounded replay levels.
    # Keep the legacy discrepancy visible without overwriting its original rows.
    rebuilt, legacy_difference = [], 0.0
    with localcontext() as context:
        context.prec = 40
        total = Decimal(str(nav))
        cash = total - sum(Decimal(str(r['current_market_value'])) for r in maps)
        units = [(r['source_symbol'], Decimal(str(r['current_market_value'])) / Decimal(str(prices[(r['source_symbol'], calendar[0])]))) for r in maps]
        peak = Decimal(1)
        for row, day in zip(stored, calendar):
            level = (cash + sum(unit*Decimal(str(prices[(symbol,day)])) for symbol,unit in units))/total
            require(level > 0, 'Source stress NAV is nonpositive')
            peak = max(peak,level);dd = level/peak-1
            independent = (1-sum(float(r['current_market_value'])/nav for r in maps)
                +sum(float(r['current_market_value'])/nav*prices[(r['source_symbol'],day)]
                     /prices[(r['source_symbol'],calendar[0])] for r in maps))
            require(math.isclose(float(level),independent,rel_tol=0,abs_tol=2e-12),
                    'Rebuilt sleeve fails independent Decimal reconciliation')
            legacy_difference = max(legacy_difference, abs(float(level)-finite(row['normalized_value'])),
                                    abs(float(dd)-finite(row['drawdown'])))
            rebuilt.append({'date':day,'value':float(level)})
    source = {'subject_id': subject, 'holdings_as_of': holdings_day, 'reporting_currency': 'USD',
              'calculation_version': SLEEVE_VERSION, 'legacy_calculation_version': SOURCE_VERSION,
              'legacy_maximum_absolute_difference':legacy_difference, 'independent_decimal_verified': True,
              'direct_components': sum(r['mapping_method']=='direct' for r in maps),
              'proxy_components': sum(r['mapping_method']=='market_proxy' for r in maps),
              'source_sha256': sha({'nav':navs,'holdings':held,'mappings':maps,'prices':used_prices,
                                    'replay':stored,'fx':consumed_fx}),
              'reconciliation_inputs':{'nav':nav,'components':[{'value':float(r['current_market_value']),
                  'symbol':r['source_symbol']} for r in maps],
                  'prices':{symbol:[prices[symbol,day] for day in calendar] for symbol in sorted(symbols)}},
              'daily': rebuilt}
    if identity_proxy:
        source['identity_proxy_policy_version'] = IDENTITY_PROXY_VERSION
        source['identity_proxy_evidence'] = {'original_mappings': original_maps, 'effective_mappings': maps}
        source['source_sha256'] = sha({'original_source_sha256': source['source_sha256'],
                                     'identity_proxy_evidence': source['identity_proxy_evidence']})
    return source


def validate_sleeve(source, calendar):
    proof=source['reconciliation_inputs'];nav=finite(proof['nav'],positive=True)
    components=proof['components'];prices=proof['prices']
    require(len(components)==source['direct_components']+source['proxy_components']>0,
            'Source component counts differ')
    require(set(prices)=={r['symbol'] for r in components}, 'Source price identities differ')
    require(all(len(series)==len(calendar) for series in prices.values()), 'Incomplete source price proof')
    with localcontext() as context:
        context.prec=40
        total=Decimal(str(nav));cash=total-sum(Decimal(str(finite(r['value']))) for r in components)
        units=[(r['symbol'],Decimal(str(r['value']))/Decimal(str(finite(prices[r['symbol']][0],positive=True)))) for r in components]
        expected=[float((cash+sum(unit*Decimal(str(finite(prices[symbol][i],positive=True))) for symbol,unit in units))/total)
                  for i in range(len(calendar))]
    require([r['date'] for r in source['daily']]==calendar and
            [r['value'] for r in source['daily']]==expected, 'Source path does not match its independent price proof')


def compute(inputs):
    """All composite arithmetic and independent Decimal verification are pure."""
    market = market_sources();days=market['calendar']
    require(inputs['subject_id']=='alpha-world-ms' and inputs['reporting_currency']=='HKD', 'MS HKD stress input required')
    require(inputs['contract_sha256']==configuration_sha256(approved_reference_definitions()['alpha-world-ms']), 'Approved MS composition differs')
    require(inputs['holdings_as_of'] <= inputs['requested_as_of'], 'Future holdings cannot enter stress replay')
    values=inputs['component_values'];require(set(values)==COMPONENTS, 'Incomplete or different MS sleeves')
    values={key:finite(value) for key,value in values.items()}
    nav=finite(inputs['nav'],positive=True)
    require(math.isclose(sum(values.values()),nav,rel_tol=1e-10,abs_tol=1e-9), 'MS components do not reconcile to HKD NAV')
    require(values['constant-cash']==58.0 and all(values[k]>0 for k in SLEEVES)
            and all(values[k]<0 for k in ('hang-seng-price-index','spy-settlement')), 'MS cash or exposure direction differs')
    fx,fx_audit=align(market['fx']['observations'],days,'rate')
    hsi,hsi_audit=align(market['hedges']['hsi']['observations'],days,'price',hong_kong_sessions=True)
    spy,spy_audit=align(market['hedges']['spy']['observations'],days,'price')
    paths={}
    identity_proxy = 'identity_proxy_policy_version' in inputs
    if identity_proxy:
        require(inputs['identity_proxy_policy_version'] == IDENTITY_PROXY_VERSION, 'Unapproved MS crisis identity policy')
    require(set(inputs['sleeves'])==set(SLEEVES), 'Missing stress sleeve')
    for subject,source in inputs['sleeves'].items():
        if identity_proxy:
            require(source.get('identity_proxy_policy_version') == IDENTITY_PROXY_VERSION, 'MS source policy missing')
            proof = source['identity_proxy_evidence']
            expected = [apply_identity_proxy(r, subject_id='alpha-world-ms', crisis_start=days[0])
                        for r in proof['original_mappings']] if subject == 'U16088571' else proof['original_mappings']
            require(expected == proof['effective_mappings'], 'MS crisis proxy evidence differs')
            require(source['reconciliation_inputs']['components'] == [
                {'value':float(r['current_market_value']), 'symbol':r['source_symbol']} for r in expected],
                'MS crisis price proof does not use the approved proxy')
        require(source['subject_id']==subject and source['reporting_currency']=='USD'
                and source['calculation_version']==SLEEVE_VERSION and source['independent_decimal_verified'] is True
                and source['holdings_as_of']==inputs['holdings_as_of'], 'Source sleeve stress qualification differs')
        require(len(source['source_sha256'])==64 and all(c in '0123456789abcdef' for c in source['source_sha256']), 'Source replay fingerprint missing')
        validate_sleeve(source,days)
        rows=source['daily'];require([r['date'] for r in rows]==days, 'Source stress dates differ')
        normalized=[finite(r['value'],positive=True) for r in rows]
        require(math.isclose(normalized[0],1.,abs_tol=1e-12), 'Source stress opening is not one')
        paths[subject]={day:level*fx[day]/fx[days[0]] for day,level in zip(days,normalized)}
    paths['hang-seng-price-index']={day:hsi[day]/hsi[days[0]] for day in days}
    paths['spy-settlement']={day:spy[day]*fx[day]/(spy[days[0]]*fx[days[0]]) for day in days}
    daily=[];peak=1.
    with localcontext() as context:
        context.prec=40;decimal_peak=Decimal(1)
        for day in days:
            level=(values['constant-cash']+sum(values[k]*paths[k][day] for k in paths))/nav
            require(math.isfinite(level) and level>0, 'MS stress produces nonpositive NAV')
            peak=max(peak,level);dd=level/peak-1
            terms=[]
            for k in SLEEVES:
                raw=inputs['sleeves'][k]['daily'][days.index(day)]['value']
                terms.append(Decimal(str(values[k]))*Decimal(str(raw))*Decimal(str(fx[day]))/Decimal(str(fx[days[0]])))
            terms.append(Decimal(str(values['hang-seng-price-index']))*Decimal(str(hsi[day]))/Decimal(str(hsi[days[0]])))
            terms.append(Decimal(str(values['spy-settlement']))*Decimal(str(spy[day]))*Decimal(str(fx[day]))/(Decimal(str(spy[days[0]]))*Decimal(str(fx[days[0]]))))
            independent=(Decimal(str(values['constant-cash']))+sum(terms))/Decimal(str(nav))
            decimal_peak=max(decimal_peak,independent)
            require(math.isclose(float(independent),level,rel_tol=0,abs_tol=2e-12)
                    and math.isclose(float(independent/decimal_peak-1),dd,rel_tol=0,abs_tol=2e-12), 'Independent HKD composite reconciliation failed')
            daily.append({'observation_date':day,'normalized_value':level,'drawdown':dd})
    return {'subject_id':'alpha-world-ms','reporting_currency':'HKD','calculation_version':IDENTITY_VERSION if identity_proxy else VERSION,
            'requested_as_of':inputs['requested_as_of'],'holdings_as_of':inputs['holdings_as_of'],
            'crisis_start':days[0],'crisis_end':days[-1],'observations':len(days),
            'maximum_drawdown':min(r['drawdown'] for r in daily),'cash_weight':values['constant-cash']/nav,
            'direct_components':2+sum(s['direct_components'] for s in inputs['sleeves'].values()),
            'proxy_components':sum(s['proxy_components'] for s in inputs['sleeves'].values()),
            'quality_status':'warn','qualification_status':'qualified_internal','independent_decimal_verified':True,
            'provider':market['provider'],'price_type':'USD sleeve replay; unadjusted hedge closes; official HKMA FX',
            'market_source_sha256':MARKET_SHA256,'input_sha256':sha(inputs),'inputs':inputs,'daily':daily,
            'fx_and_hedge_alignment_sha256':sha([fx_audit,hsi_audit,spy_audit]),
            'cash_interest':'none','projected_financing':'none','publication_status':'blocked_provisional_market_data',
            'limitations':'Hypothetical as-of-holdings stress replay with documented proxies. Constant HKD cash. No projected financing or trading costs. Internal review only.'}


def validate(replay, *, as_of=None, holdings=None, nav=None):
    require(isinstance(replay,dict) and replay.get('calculation_version') in (VERSION, IDENTITY_VERSION), 'Qualified HKD MS stress replay required')
    require(compute(replay['inputs'])==replay, 'MS stress payload differs from deterministic verification')
    if as_of is not None:require(replay['holdings_as_of']==as_of, 'Stress holdings date differs')
    if holdings is not None:
        require({r['conid']:float(r['market_value_text']) for r in holdings}==replay['inputs']['component_values']
                and all(r['currency']=='HKD' for r in holdings), 'Stress uses another MS composition/currency')
    if nav is not None:require(float(nav)==replay['inputs']['nav'], 'Stress uses another MS NAV')
    return replay


def from_capture(records, snapshot, *, identity_proxy=False):
    day=snapshot['data_as_of'];holding_rows=snapshot['holdings']['holdings']
    require(all(r['currency']=='HKD' for r in holding_rows), 'Frozen stress holdings are not HKD')
    inputs={'subject_id':'alpha-world-ms','reporting_currency':'HKD','requested_as_of':snapshot['period_end'],
            'holdings_as_of':day,'contract_sha256':configuration_sha256(approved_reference_definitions()['alpha-world-ms']),
            'nav':float(snapshot['portfolio_summary']['latest_nav']['total_text']),
            'component_values':{r['conid']:float(r['market_value_text']) for r in holding_rows},
            'sleeves':{subject:verify_sleeve(records,subject,day,market_sources()['calendar'], identity_proxy=identity_proxy) for subject in SLEEVES}}
    if identity_proxy: inputs['identity_proxy_policy_version'] = IDENTITY_PROXY_VERSION
    result=compute(inputs)
    validate(result,as_of=day,holdings=holding_rows,nav=inputs['nav'])
    return result


def load(connection, snapshot, *, tenant_id=None, identity_proxy=False):
    """Read-only producer: exact as-of inputs, no fetches or source-table writes."""
    from .subject_read_contract import require_ms_hkd_contract
    require_ms_hkd_contract(connection,tenant_id=tenant_id)
    day=str(snapshot['data_as_of'])
    clause,params=('tenant_id = ? AND ',(tenant_id,)) if tenant_id else ('',())
    if 'holdings' not in snapshot:
        import copy
        snapshot=copy.deepcopy(snapshot)
        components=connection.execute('SELECT component_id,contribution,base_currency FROM synthetic_portfolio_components WHERE '
            +clause+"portfolio_id = 'alpha-world-ms' AND snapshot_date = ? AND base_currency = 'HKD' ORDER BY component_id",params+(day,)).fetchall()
        snapshot['holdings']={'holdings':[{'conid':str(r['component_id']),'currency':str(r['base_currency']),
            'market_value_text':str(r['contribution'])} for r in components]}
        snapshot['portfolio_summary']={'latest_nav':{'total_text':str(snapshot['performance']['series'][-1]['nav'])}}
    def rows(table,where,args,order):
        result=connection.execute('SELECT * FROM '+table+' WHERE '+clause+where+' ORDER BY '+order,
                                  params+tuple(args)).fetchall()
        return database_records(result)
    subjects=list(SLEEVES);placeholders=','.join('?' for _ in subjects)
    where='subject_id IN ('+placeholders+') AND '
    records={
        'source_nav':rows('subject_nav_series',where+'snapshot_date = ?',[*subjects,day],'subject_id,snapshot_date'),
        'source_positions':rows('subject_position_series',where+'snapshot_date = ?',[*subjects,day],'subject_id,snapshot_date,conid'),
        'mappings':rows('crisis_replay_components',where+'holdings_as_of = ?',[*subjects,day],'subject_id,holdings_as_of,conid'),
        'replays':rows('crisis_replay_daily',where+'holdings_as_of = ?',[*subjects,day],'subject_id,holdings_as_of,observation_date'),
        'prices':rows('market_data_prices',"instrument_id LIKE ? AND price_date BETWEEN ? AND ?",
                      ['crisis-replay-%','2008-09-05','2009-03-09'],'symbol,price_date'),
        'source_fx':rows('reference_fx_rates',"from_currency = 'HKD' AND to_currency = 'USD' AND rate_date BETWEEN ? AND ?",
                         [(date.fromisoformat(day)-timedelta(days=4)).isoformat(),day],'rate_date,provider')}
    return from_capture(records,snapshot,identity_proxy=identity_proxy)


def qualified_snapshot(snapshot,replay):
    """Create a new private report input; never mutate a frozen input in place."""
    import copy
    require(snapshot.get('subject_id')=='alpha-world-ms' and snapshot.get('reporting_currency')=='HKD'
            and not snapshot.get('publication_allowed'), 'Private HKD MS snapshot required')
    validate(replay,as_of=snapshot['data_as_of'],holdings=snapshot['holdings']['holdings'],
             nav=snapshot['portfolio_summary']['latest_nav']['total_text'])
    result=copy.deepcopy(snapshot)
    result['risk']['crisis_replay']=copy.deepcopy(replay)
    result['publication_blockers']=[b for b in result['publication_blockers'] if b!='risk:crisis_replay_unavailable']
    result['quality_status']='warn'
    result['snapshot_version']=str(result['snapshot_version'])+'+'+VERSION
    return result
