"""Read freshly downloaded broker statements; never import or delete live rows."""
import json,hashlib,sys
from pathlib import Path
from collections import defaultdict,Counter
from decimal import Decimal as D
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.parser import parse_flex_xml
W=ROOT/'.work/alpha-t-onboarding-completion-20261009'
def key(account,date,currency,amount,description,conid,event):
    return (account,date,currency,D(amount),description or '',conid or '',event or '')
def main():
    ledger=json.loads((W/'supplemental-inputs.json').read_bytes())['cash_transactions']
    idx=defaultdict(list);receipts=[];bridges=[];ids=defaultdict(list)
    for path in sorted((W/'fresh-broker').glob('*.xml')):
        raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest();root=ET.fromstring(raw)
        periods=[]
        for st in root.iter('FlexStatement'):
            account=st.get('accountId');assert account in ('U16088571','U16088606')
            periods.append(st.attrib)
            cash=list(st.iter('CashTransaction'));details=[r for r in cash if r.get('accountId')==account and r.get('transactionID')]
            other=[r for r in cash if r.get('accountId') not in ('-',account) or (r.get('accountId')==account and not r.get('transactionID'))]
            assert not other,'Unidentified cash detail rows need review'
            for r in details:ids[(account,r.get('transactionID'))].append(r.attrib)
            for report in st.iter('CashReportCurrency'):
                ccy=report.get('currency')
                if ccy=='BASE_SUMMARY':
                    if account!='U16088571':continue
                    ccy='USD'
                for field,typ in [('dividends','Dividends'),('withholdingTax','Withholding Tax')]:
                    total=sum((D(r.get('amount')) for r in details if r.get('currency')==ccy and r.get('type')==typ),D(0))
                    expected=D(report.get(field,'0'))
                    bridges.append({'account':account,'from':st.get('fromDate'),'to':st.get('toDate'),'currency':ccy,'category':typ,'detail_sum':str(total),'statement_total':str(expected),'matched':total==expected,'source_sha256':sha})
        for row in parse_flex_xml(raw).rows:
            if row.record_type!='CashTransaction':continue
            a=row.attributes
            idx[key(row.account_id,row.report_date,row.currency,a['amount'],a.get('description'),row.conid,a.get('dateTime'))].append({'type':a.get('type'),'transaction_id':a.get('transactionID'),'trade_id':a.get('tradeID'),'row_account':a.get('accountId'),'source_sha256':sha,'row_sha256':row.row_sha256})
        receipts.append({'file':path.name,'sha256':sha,'bytes':len(raw),'periods':periods})
    matched=[];missing=[];ambiguous=[]
    for r in ledger:
        hits=idx.get(key(r['account_id'],r['effective_date'],r['currency'],r['amount_text'],r['description'],r['conid'],r['event_datetime']),[])
        identity={'account':r['account_id'],'source_key':r['source_key'],'old_row_sha256':r['row_sha256']}
        unique={json.dumps(h,sort_keys=True):h for h in hits}
        if len(unique)==1:matched.append({**identity,'fresh':next(iter(unique.values()))})
        elif not hits:missing.append(identity)
        else:ambiguous.append({**identity,'candidates':list(unique.values())})
    dup=[{'account':k[0],'transaction_id':k[1],'rows':len(v)} for k,v in ids.items() if len(v)>1]
    result={'database_changed':False,'existing_rows':len(ledger),'exact_event_matches':len(matched),'with_recoverable_detail_id':sum(bool(r['fresh']['transaction_id']) for r in matched),'summary_or_no_id_matches':sum(not r['fresh']['transaction_id'] for r in matched),'unmatched':len(missing),'ambiguous':len(ambiguous),'fresh_detail_rows':sum(len(v) for v in ids.values()),'duplicate_detail_ids':dup,'statement_bridges':bridges,'originals':receipts,'matched':matched,'missing':missing,'ambiguous_rows':ambiguous,'note':'Private correspondence only; missing coverage may reflect export period, broker corrections or absent events. Summary rows retained as raw evidence, not additional economic cash.'}
    dest=W/('fresh-cash-reconciliation-'+str(len(receipts))+'.json')
    with dest.open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in ('matched','missing','ambiguous_rows','originals','statement_bridges')}))
    print(json.dumps({'bridges':len(bridges),'matched':sum(r['matched'] for r in bridges),'bridge_differences':[r for r in bridges if not r['matched']]}))
if __name__=='__main__':main()
