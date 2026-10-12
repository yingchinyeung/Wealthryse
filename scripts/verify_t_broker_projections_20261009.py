"""Private field-by-field original-broker comparison. No ledger writes or deduplication."""
from pathlib import Path
from collections import defaultdict,Counter
from decimal import Decimal as D
import json,hashlib,sys
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.parser import parse_flex_xml
from ibkr_etl.util import pick,parse_date_text,decimal_text
W=ROOT/'.work/alpha-t-onboarding-completion-20261009'

def projection(kind,row):
    a=row.attributes
    def dec(*keys):return decimal_text(pick(a,*keys))
    base={'currency':row.currency}
    if kind=='trades':
        return {**base,'execution_id':pick(a,'ibExecID'),'trade_id':pick(a,'tradeID'),'conid':row.conid,
          'trade_date':parse_date_text(pick(a,'tradeDate')) or row.report_date,'trade_time':pick(a,'tradeTime','dateTime'),
          'settle_date':parse_date_text(pick(a,'settleDateTarget','settleDate')),'buy_sell':pick(a,'buySell','side'),
          'quantity_text':dec('quantity'),'trade_price_text':dec('tradePrice','price'),'proceeds_text':dec('proceeds','tradeMoney'),
          'commission_text':dec('ibCommission','commission'),'commission_currency':pick(a,'ibCommissionCurrency','commissionCurrency'),
          'net_cash_text':dec('netCash'),'realized_pnl_text':dec('fifoPnlRealized','realizedPL'),
          'order_reference':pick(a,'orderReference','orderRef'),'model_code':pick(a,'model','modelCode')}
    if kind=='corporate_actions':
        return {**base,'action_id':pick(a,'actionID'),'effective_date':row.report_date,'action_type':pick(a,'type','actionType','code'),
          'description':pick(a,'description'),'quantity_text':dec('quantity'),'amount_text':dec('amount','proceeds'),'conid':row.conid}
    if kind=='cash_snapshots':
        return {**base,'snapshot_date':row.report_date,'ending_cash_text':dec('endingCash','cashBalance'),
          'settled_cash_text':dec('endingSettledCash','settledCash'),'fx_rate_to_base_text':dec('fxRateToBase','exchangeRate')}
    if kind=='position_details':
        return {**base,'snapshot_date':row.report_date,'conid':row.conid,'model_code':pick(a,'model','modelCode'),
          'quantity_text':dec('position','quantity'),'cost_basis_text':dec('costBasisMoney','costBasis'),
          'mark_price_text':dec('markPrice','closePrice'),'market_value_text':dec('positionValue','marketValue'),
          'unrealized_pnl_text':dec('fifoPnlUnrealized','unrealizedPL')}
    if kind=='broker_fx':
        return {'rate_date':row.report_date,'from_currency':pick(a,'fromCurrency','currency'),
          'to_currency':pick(a,'toCurrency','baseCurrency',default='BASE'),'rate_text':dec('rate','fxRateToBase','exchangeRate')}
    if kind=='nav_details':
        accrual_keys=('cfdUnrealizedPl','forexCfdUnrealizedPl','cgtWithholdingAccruals','dividendAccruals','interestAccruals','marginFinancingChargeAccruals')
        def total(keys):
            nums=[D(a[k]) for k in keys if a.get(k) not in (None,'')]
            return str(sum(nums)) if nums else None
        return {'currency':row.currency or pick(a,'baseCurrency',default='BASE'),'asset_class':pick(a,'assetClass','assetCategory',default='TOTAL'),
          'snapshot_date':row.report_date,'total_text':dec('total','endingValue','netLiquidationValue','nav') or total(('cash','stock','options','bonds','commodities')+accrual_keys),
          'cash_text':dec('cash'),'stock_text':dec('stock'),'options_text':dec('options','option'),'futures_text':dec('futures','future'),
          'bonds_text':dec('bonds','bond'),'accruals_text':dec('accruals') or total(accrual_keys)}
    raise ValueError(kind)

def main():
    live=json.loads((ROOT/'.work/alpha-world-t-accuracy-audit-20261009/live-inputs.json').read_bytes())
    more=json.loads((W/'supplemental-inputs.json').read_bytes())
    tables={k:(more if k in more else live)[k] for k in ('trades','corporate_actions','cash_snapshots','position_details','broker_fx','nav_details')}
    needed={(r['account_id'],r['row_sha256']) for rows in tables.values() for r in rows};found={}
    archive=json.loads((W/'archive-capture.json').read_bytes())
    for i,item in enumerate(archive['results']):
        if item['status']!='captured':continue
        raw=(W/'originals'/(item['sha256']+'.xml')).read_bytes();assert hashlib.sha256(raw).hexdigest()==item['sha256']
        for row in parse_flex_xml(raw).rows:
            key=row.account_id,row.row_sha256
            if key in needed and key not in found:found[key]=(row,item['sha256'])
        if (i+1)%100==0:print(json.dumps({'archives':i+1,'matches':len(found)}),flush=True)
    summary={};details={}
    for kind,rows in tables.items():
        issues=[];checked=0
        for r in rows:
            hit=found.get((r['account_id'],r['row_sha256']))
            if not hit:issues.append({'account':r['account_id'],'row_sha256':r['row_sha256'],'reason':'original absent'});continue
            expected=projection(kind,hit[0]);diff={}
            for k,v in expected.items():
                actual=r[k];equal=actual==v
                if k.endswith('_text') and actual is not None and v is not None:equal=D(actual)==D(v)
                if not equal:diff[k]={'database':actual,'original':v}
            if diff:issues.append({'account':r['account_id'],'row_sha256':r['row_sha256'],'source_sha256':hit[1],'differences':diff})
            checked+=1
        summary[kind]={'rows':len(rows),'originals_found':checked,'rows_with_issues':len(issues)};details[kind]=issues
    result={'summary':summary,'issues':details,'database_changed':False,'deletions':0,'scope':'Original attribute projection fidelity, not independent verification of broker economic correctness'}
    with (W/'broker-field-verification.json').open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps(summary))

if __name__=='__main__':main()
