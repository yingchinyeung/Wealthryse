"""Exports use the same tenant-scoped subject views as account pages."""
from fastapi import HTTPException
from .subject_read_contract import synthetic_exposure

PERFORMANCE = ('snapshot_date','currency','total_text')
HOLDINGS = ('snapshot_date','conid','symbol','description','currency','quantity_text','mark_price_text','market_value_text')
CASH = ('effective_date','event_datetime','transaction_type','description','amount_text','currency','conid')
TRADES = ('trade_date','trade_time','settle_date','execution_id','trade_id','conid','symbol','description','asset_category','buy_sell','quantity_text','trade_price_text','currency','proceeds_text','commission_text','commission_currency','net_cash_text','realized_pnl_text')
EXPOSURES = ('snapshot_date','component_id','component_name','component_type','base_currency','source_currency','coefficient','source_value','fx_rate_to_base','base_value','normalized_value','market_value_text','direction','observation_date','holding_kind','valuation_basis')

def subject_export(connection, *, tenant_id, subject_id, access, dataset, requested_as_of, from_date, symbol, side, cash_filter, trade_filter):
    if dataset not in ('performance','holdings','cash-activity','trades'):
        raise HTTPException(404,'Export dataset not available')
    derived=access.get('subject_type','physical_account') in ('filtered_portfolio','composite_portfolio')
    if dataset=='trades' and derived:
        raise HTTPException(409,'This derived portfolio has no independently executed trades. Source-account executions are not portfolio executions. Use holdings, component exposures or reconstructed cash activity.')
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
        rows=connection.execute('''SELECT t.trade_date,t.trade_time,t.settle_date,t.execution_id,t.trade_id,t.conid,i.symbol,i.description,i.asset_category,t.buy_sell,t.quantity_text,t.trade_price_text,t.currency,t.proceeds_text,t.commission_text,t.commission_currency,t.net_cash_text,t.realized_pnl_text FROM trades t JOIN accounts a ON a.account_id=t.account_id LEFT JOIN instruments i ON i.conid=t.conid WHERE a.tenant_id=%s AND t.account_id=%s AND t.trade_date BETWEEN %s AND %s AND (%s::text IS NULL OR POSITION(UPPER(%s) IN UPPER(COALESCE(i.symbol,'')))>0) AND (%s::text IS NULL OR UPPER(COALESCE(t.buy_sell,''))=%s) ORDER BY t.trade_date,t.trade_time,t.source_key''',(tenant_id,subject_id,start,end,normalized_symbol,normalized_symbol,normalized_side,normalized_side)).fetchall()
    return columns,rows,effective,start
