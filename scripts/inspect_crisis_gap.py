"""Fixed-scope read-only preflight for the sampled historical crisis gap.

Only database-resident, pre-existing mappings/prices are inspected. No Yahoo
requests, Flex requests, substitute mappings or portfolio values are logged.
"""
import json
import math
from decimal import Decimal, localcontext
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "src"))
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.crisis_replay import ReplayComponent, calculate_fixed_unit_replay, CALCULATION_VERSION

SUBJECT = "U16088571"
DAY = "2025-11-25"


def verify_stored_replay(holdings, mappings, nav, observations, legacy, *, include_replay=False):
    """Recompute only a complete, exact-date USD mapping; never invent a proxy."""
    nav = float(nav)
    if not math.isfinite(nav) or nav <= 0:
        raise ValueError('Invalid NAV')
    held = {str(row['conid']):row for row in holdings}
    mapped = {str(row['conid']):row for row in mappings}
    if not held or len(held)!=len(holdings) or len(mapped)!=len(mappings) or set(held)!=set(mapped):
        raise ValueError('Holdings and exact-date mapping identities differ')
    components=[]
    for conid,row in mapped.items():
        value=float(held[conid]['market_value'])
        if (held[conid]['currency']!='USD' or row['currency']!='USD'
                or row['price_type']!='adjusted_close' or not math.isfinite(value)
                or not math.isclose(float(row['current_market_value']),value,rel_tol=1e-10,abs_tol=0.000001)
                or not math.isclose(float(row['opening_weight']),value/nav,rel_tol=1e-10,abs_tol=1e-10)):
            raise ValueError('Stored component no longer reconciles to holdings/NAV')
        components.append(ReplayComponent(conid,str(row['holding_symbol']),str(row['description']),
            'USD',value,str(row['source_symbol']),str(row['mapping_method']),row['proxy_reason']))
    needed={c.source_symbol for c in components}|{'SPY'}
    prices={symbol:{} for symbol in needed}
    for row in observations:
        if row['symbol'] not in needed:
            continue
        day=str(row['price_date'])
        value=float(row['price'])
        if (row['currency']!='USD' or row['price_type']!='adjusted_close'
                or not math.isfinite(value) or value <= 0 or day in prices[row['symbol']]):
            raise ValueError('Invalid or ambiguous stored price')
        prices[row['symbol']][day]=value
    calendar=sorted(prices['SPY'])
    if len(calendar)!=121:
        raise ValueError('Incomplete stored crisis calendar')
    replay=calculate_fixed_unit_replay(components=components,portfolio_nav=nav,prices=prices,calendar=calendar)
    # Independent high-precision fixed-unit valuation, rather than a second
    # call to the same implementation. The residual cash earns no interest.
    with localcontext() as context:
        context.prec=40
        total=Decimal(str(nav))
        cash=total-sum(Decimal(str(c.market_value)) for c in components)
        units=[Decimal(str(c.market_value))/Decimal(str(prices[c.source_symbol][calendar[0]])) for c in components]
        peak=Decimal(1)
        for day,result in zip(calendar,replay['daily']):
            level=(cash+sum(u*Decimal(str(prices[c.source_symbol][day])) for u,c in zip(units,components)))/total
            peak=max(peak,level)
            dd=level/peak-1
            if not math.isclose(float(level),float(result['normalized_value']),rel_tol=0,abs_tol=1e-12) or not math.isclose(float(dd),float(result['drawdown']),rel_tol=0,abs_tol=1e-12):
                raise ValueError('Independent decimal replay differs from engine')
    old={str(row['observation_date']):row for row in legacy}
    if len(old)!=len(legacy) or set(old)!=set(calendar):
        raise ValueError('Legacy replay calendar differs')
    matches=all(math.isclose(float(old[row['observation_date']][key]),float(row[key]),
                            rel_tol=1e-9,abs_tol=1e-9)
                for row in replay['daily'] for key in ('normalized_value','drawdown'))
    result={'target_version':CALCULATION_VERSION,'observations':len(calendar),
            'independent_decimal_verified':True,
            'exact_holdings_reconciled':True,'legacy_values_match':matches,
            'maximum_path_difference':max(abs(float(old[r['observation_date']]['normalized_value'])-float(r['normalized_value'])) for r in replay['daily']),
            'maximum_drawdown_difference':max(abs(float(old[r['observation_date']]['drawdown'])-float(r['drawdown'])) for r in replay['daily']),
            'legacy_opening_value':float(old[calendar[0]]['normalized_value']),
            'recomputed_opening_value':float(replay['daily'][0]['normalized_value']),
            'legacy_maximum_drawdown':min(float(r['drawdown']) for r in legacy),
            'recomputed_maximum_drawdown':replay['maximum_drawdown'],
            'database_changed':False}
    if include_replay:
        result['replay']=replay
    return result


def main():
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20,
                         options="-c default_transaction_read_only=on -c statement_timeout=60000",
                         row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SELECT set_config('app.tenant_id',%s,true)", (os.environ["PORTFOLIO_TENANT_ID"],))
        rows = connection.execute("""SELECT DISTINCT p.conid, p.currency, i.symbol,
             m.source_symbol, m.mapping_method, m.holdings_as_of AS mapping_date, m.price_type
          FROM subject_position_series p LEFT JOIN instruments i ON i.conid=p.conid
          LEFT JOIN LATERAL (
            SELECT source_symbol,mapping_method,holdings_as_of,price_type FROM crisis_replay_components c
            WHERE c.subject_id=p.subject_id AND c.conid=p.conid AND c.holdings_as_of <= %s
            ORDER BY c.holdings_as_of DESC LIMIT 1
          ) m ON true
          WHERE p.tenant_id=app_tenant_id() AND p.subject_id=%s AND p.snapshot_date=%s
            AND UPPER(COALESCE(i.symbol,'')) NOT LIKE '%%.REC'
          ORDER BY p.conid""", (DAY,SUBJECT,DAY)).fetchall()
        prices = connection.execute("""SELECT symbol, currency, price_type,COUNT(*) AS observations,
             MIN(price_date) AS first_date, MAX(price_date) AS last_date
           FROM market_data_prices WHERE tenant_id=app_tenant_id()
             AND instrument_id LIKE 'crisis-replay-%%'
             AND price_date BETWEEN '2008-09-15' AND '2009-03-09'
           GROUP BY symbol,currency,price_type ORDER BY symbol""").fetchall()
        versions = connection.execute("""SELECT calculation_version, quality_status, COUNT(*) AS observations,
             MIN(observation_date) AS first_date, MAX(observation_date) AS last_date
          FROM crisis_replay_daily WHERE tenant_id=app_tenant_id() AND subject_id=%s AND holdings_as_of=%s
          GROUP BY calculation_version,quality_status""", (SUBJECT,DAY)).fetchall()
        needed = {r['source_symbol'] for r in rows if r['source_symbol']} | {'SPY'}
        covered = {r['symbol'] for r in prices if r['currency']=='USD' and r['price_type']=='adjusted_close'
                   and r['observations']==121 and r['first_date']=='2008-09-15' and r['last_date']=='2009-03-09'}
        holdings=connection.execute("""SELECT p.conid,p.currency,
              SUM(CAST(p.market_value_text AS DOUBLE PRECISION)) AS market_value
            FROM subject_position_series p LEFT JOIN instruments i ON i.conid=p.conid
            WHERE p.tenant_id=app_tenant_id() AND p.subject_id=%s AND p.snapshot_date=%s
              AND UPPER(COALESCE(i.symbol,'')) NOT LIKE '%%.REC'
            GROUP BY p.conid,p.currency""",(SUBJECT,DAY)).fetchall()
        mappings=connection.execute("""SELECT conid,holding_symbol,description,currency,
              current_market_value,opening_weight,source_symbol,mapping_method,proxy_reason,price_type
            FROM crisis_replay_components WHERE tenant_id=app_tenant_id()
              AND subject_id=%s AND holdings_as_of=%s""",(SUBJECT,DAY)).fetchall()
        nav=connection.execute("""SELECT total_text FROM subject_nav_series
            WHERE tenant_id=app_tenant_id() AND subject_id=%s AND snapshot_date=%s""",(SUBJECT,DAY)).fetchall()
        if len(nav)!=1:
            raise ValueError('Ambiguous or missing NAV')
        observations=connection.execute("""SELECT symbol,price_date,price,currency,price_type
            FROM market_data_prices WHERE tenant_id=app_tenant_id()
              AND instrument_id LIKE 'crisis-replay-%%'
              AND price_date BETWEEN '2008-09-15' AND '2009-03-09'""").fetchall()
        legacy=connection.execute("""SELECT observation_date,normalized_value,drawdown
            FROM crisis_replay_daily WHERE tenant_id=app_tenant_id() AND subject_id=%s
              AND holdings_as_of=%s""",(SUBJECT,DAY)).fetchall()
        recomputed=verify_stored_replay(holdings,mappings,nav[0]['total_text'],observations,legacy)
        print('CRISIS_GAP ' + json.dumps({"subject_id":SUBJECT,"as_of":DAY,
            "holdings":len(rows), "currencies":sorted({r['currency'] for r in rows}),
            "unmapped":sum(not r['source_symbol'] for r in rows),
            "same_date_mappings":sum(str(r['mapping_date'])==DAY for r in rows),
            "needed_price_series":len(needed), "missing_complete_series":sorted(needed-covered),
            "existing_daily_versions":versions, "recomputation":recomputed,
            "mode":"read_only"},default=str),flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print('CRISIS_GAP ' + json.dumps({"error_type":type(error).__name__}),flush=True)
        sys.exit(1)
