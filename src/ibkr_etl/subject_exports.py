"""Exports use the same tenant-scoped subject views as account pages."""
from fastapi import HTTPException
from .subject_read_contract import synthetic_exposure
from .derived_portfolio import DUPLICATE_TRADE_REASON, EXCLUDED_CURRENCY_TRADE_REASON, EXCLUDED_INSTRUMENT_TRADE_REASON_PREFIX

PERFORMANCE = ('snapshot_date','currency','total_text')
HOLDINGS = ('snapshot_date','conid','symbol','description','currency','quantity_text','mark_price_text','market_value_text')
CASH = ('effective_date','event_datetime','transaction_type','description','amount_text','currency','conid')
TRADES = ('trade_date','trade_time','settle_date','execution_id','trade_id','conid','symbol','description','asset_category','buy_sell','quantity_text','trade_price_text','currency','proceeds_text','commission_text','commission_currency','net_cash_text','realized_pnl_text')
EXPOSURES = ('snapshot_date','component_id','component_name','component_type','base_currency','source_currency','coefficient','source_value','fx_rate_to_base','base_value','normalized_value','market_value_text','direction','observation_date','holding_kind','valuation_basis')

# A filtered portfolio's trades are its source account's executions in the
# instruments it keeps. Each source trade has one primary ledger event (":net"
# for securities, ":base" for FX); a trade is shown unless that event records an
# instrument, excluded-currency or duplicate exclusion. Cash-policy reasons such
# as a non-permitted settlement currency do not remove the execution itself.
FILTERED_TRADES_SQL = '''SELECT t.trade_date,t.trade_time,t.settle_date,t.execution_id,t.trade_id,t.conid,i.symbol,i.description,i.asset_category,t.buy_sell,t.quantity_text,t.trade_price_text,t.currency,t.proceeds_text,t.commission_text,t.commission_currency,t.net_cash_text,t.realized_pnl_text{extra} FROM derived_portfolio_cash_ledger l JOIN trades t ON t.account_id=l.source_account_id AND t.source_key=l.source_key JOIN accounts a ON a.account_id=t.account_id AND a.tenant_id=l.tenant_id LEFT JOIN instruments i ON i.conid=t.conid WHERE l.tenant_id=%s AND l.portfolio_id=%s AND l.source_type IN ('security_trade','fx_trade') AND (l.event_key LIKE '%%:net' OR l.event_key LIKE '%%:base') AND (l.exclusion_reason IS NULL OR NOT (l.exclusion_reason=%s OR l.exclusion_reason=%s OR LEFT(l.exclusion_reason,%s)=%s)) AND t.trade_date BETWEEN %s AND %s AND (%s::text IS NULL OR POSITION(UPPER(%s) IN UPPER(COALESCE(i.symbol,'')))>0) AND (%s::text IS NULL OR UPPER(COALESCE(t.buy_sell,''))=%s) ORDER BY {order}'''

def filtered_trade_query(*,tenant_id,subject_id,start,end,symbol,side,latest_first=False,limit=None):
    """Return (sql, params) for a filtered portfolio's trade history."""
    order='t.trade_date DESC,t.trade_time DESC,t.source_key DESC' if latest_first else 't.trade_date,t.trade_time,t.source_key'
    sql=FILTERED_TRADES_SQL.format(extra=',COUNT(*) OVER() AS total_count' if limit else '',order=order)
    if limit:sql+=f' LIMIT {int(limit)}'
    prefix=EXCLUDED_INSTRUMENT_TRADE_REASON_PREFIX
    return sql,(tenant_id,subject_id,DUPLICATE_TRADE_REASON,EXCLUDED_CURRENCY_TRADE_REASON,len(prefix),prefix,start,end,symbol,symbol,side,side)

def subject_export(connection, *, tenant_id, subject_id, access, dataset, requested_as_of, from_date, symbol, side, cash_filter, trade_filter):
    if dataset not in ('performance','holdings','cash-activity','trades'):
        raise HTTPException(404,'Export dataset not available')
    subject_type=access.get('subject_type','physical_account')
    if dataset=='trades' and subject_type=='composite_portfolio':
        raise HTTPException(409,'This composite portfolio has no independently executed trades. Use component exposures or reconstructed cash activity.')
    currency=access['reporting_currency']
    effective=requested_as_of or connection.execute('SELECT MAX(snapshot_date) AS latest FROM subject_nav_series WHERE tenant_id=%s AND subject_id=%s AND currency=%s',(tenant_id,subject_id,currency)).fetchone()['latest']
    if effective is None:raise HTTPException(404,'No portfolio observations are available for export')
    start=from_date or str(access['history_start_date'])
    if dataset=='performance':
        columns=PERFORMANCE
        rows=connection.execute('SELECT snapshot_date,currency,total_text FROM subject_nav_series WHERE tenant_id=%s AND subject_id=%s AND currency=%s AND snapshot_date BETWEEN %s AND %s ORDER BY snapshot_date',(tenant_id,subject_id,currency,access['history_start_date'],effective)).fetchall()
    elif dataset=='holdings' and access.get('subject_type')=='composite_portfolio':
        columns=EXPOSURES
        rows=connection.execute('''SELECT snapshot_date,component_id,component_name,component_type,base_currency,source_currency,coefficient,source_value,fx_rate_to_base,base_value,normalized_value,contribution::text AS market_value_text,direction,observation_date FROM synthetic_portfolio_components WHERE tenant_id=%s AND portfolio_id=%s AND base_currency=%s AND snapshot_date=(SELECT MAX(snapshot_date) FROM synthetic_portfolio_components WHERE tenant_id=%s AND portfolio_id=%s AND base_currency=%s AND snapshot_date<=%s) ORDER BY ABS(contribution) DESC''',(tenant_id,subject_id,currency,tenant_id,subject_id,currency,effective)).fetchall()
        try:
            rows=[{**r,**synthetic_exposure({**r,'conid':r['component_id'],'symbol':r['component_id'],'description':r['component_name'],'currency':r['base_currency']})} for r in rows]
        except ValueError as error:raise HTTPException(409,'Synthetic exposures failed validation') from error
    elif dataset=='holdings':
        columns=HOLDINGS
        rows=connection.execute('''SELECT p.snapshot_date,p.conid,i.symbol,i.description,p.currency,p.quantity_text,p.mark_price_text,p.market_value_text FROM subject_position_series p LEFT JOIN instruments i ON i.conid=p.conid WHERE p.tenant_id=%s AND p.subject_id=%s AND p.snapshot_date=(SELECT MAX(snapshot_date) FROM subject_position_series WHERE tenant_id=%s AND subject_id=%s AND snapshot_date<=%s) AND COALESCE(NULLIF(p.quantity_text,''),'0')::numeric<>0 ORDER BY ABS(COALESCE(NULLIF(p.market_value_text,''),'0')::numeric) DESC''',(tenant_id,subject_id,tenant_id,subject_id,effective)).fetchall()
    elif dataset=='cash-activity':
        start,end=cash_filter(start,effective);columns=CASH
        rows=connection.execute('''SELECT effective_date,event_datetime,transaction_type,description,amount_text,currency,conid FROM subject_cash_activity_series WHERE tenant_id=%s AND subject_id=%s AND included AND effective_date BETWEEN %s AND %s ORDER BY effective_date,event_datetime,source_key''',(tenant_id,subject_id,start,end)).fetchall()
    else:
        start,end,normalized_symbol,normalized_side=trade_filter(start,effective,symbol,side);columns=TRADES
        if subject_type=='filtered_portfolio':
            sql,params=filtered_trade_query(tenant_id=tenant_id,subject_id=subject_id,start=start,end=end,symbol=normalized_symbol,side=normalized_side)
            return columns,connection.execute(sql,params).fetchall(),effective,start
        rows=connection.execute('''SELECT t.trade_date,t.trade_time,t.settle_date,t.execution_id,t.trade_id,t.conid,i.symbol,i.description,i.asset_category,t.buy_sell,t.quantity_text,t.trade_price_text,t.currency,t.proceeds_text,t.commission_text,t.commission_currency,t.net_cash_text,t.realized_pnl_text FROM trades t JOIN accounts a ON a.account_id=t.account_id LEFT JOIN instruments i ON i.conid=t.conid WHERE a.tenant_id=%s AND t.account_id=%s AND t.trade_date BETWEEN %s AND %s AND (%s::text IS NULL OR POSITION(UPPER(%s) IN UPPER(COALESCE(i.symbol,'')))>0) AND (%s::text IS NULL OR UPPER(COALESCE(t.buy_sell,''))=%s) ORDER BY t.trade_date,t.trade_time,t.source_key''',(tenant_id,subject_id,start,end,normalized_symbol,normalized_symbol,normalized_side,normalized_side)).fetchall()
    return columns,rows,effective,start
