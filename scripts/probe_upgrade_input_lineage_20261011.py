"""Read-only input lineage and numerical scale of frozen-versus-fresh differences."""
import hashlib,importlib,json,math,sys
from collections import defaultdict
import qa_read_only as q

def price_changes(old,new):
    left={(r['symbol'],str(r['price_date'])):r for r in old}
    right={(r['symbol'],str(r['price_date'])):r for r in new}
    if len(left)!=len(old) or len(right)!=len(new) or set(left)!=set(right):raise ValueError('Price identities differ')
    grouped=defaultdict(list)
    for key,a in left.items():
        b=right[key]
        x=float(a['price']);y=float(b['price'])
        if min(x,y)<=0 or not math.isfinite(x+y):raise ValueError('Invalid price')
        grouped[key[0]].append((key[1],x,y,a,b))
    result=[]
    for symbol,items in sorted(grouped.items()):
        items.sort();changed=sum(x!=y for _,x,y,_,_ in items)
        if not changed:continue
        x0,y0=items[0][1:3]
        relative=max(abs(y/x-1) for _,x,y,_,_ in items)
        path_delta=max(abs(y/y0-x/x0) for _,x,y,_,_ in items)
        conventions=all(a.get(k)==b.get(k) for _,_,_,a,b in items for k in ('provider','currency','price_type','source'))
        result.append({'symbol':symbol,'changed_prices':changed,'observations':len(items),
            'maximum_relative_price_change':relative,'maximum_normalized_path_change':path_delta,
            'provider_currency_type_source_unchanged':conventions,
            'refetched_rows':sum(a.get('fetched_at')!=b.get('fetched_at') for _,_,_,a,b in items)})
    return result

def snapshots(database):
    with q.connect(database) as db:
        rows=db.execute("SELECT subject_id,snapshot_payload_json FROM reporting_snapshots WHERE tenant_id=%s AND period_end='2026-09-30' AND status='frozen'",(q.TENANT,)).fetchall()
        return {r['subject_id']:json.loads(r['snapshot_payload_json']) for r in rows}

def main():
    sys.path[:0]=['/app/src','/app/scripts']
    a=snapshots(q.SOURCE_DB);b=snapshots(q.TEST_DB)
    old=a['alpha-world-t']['risk']['crisis_replay'];new=b['alpha-world-t']['risk']['crisis_replay']
    oldprices=old['inputs']['records']['prices'];newprices=new['inputs']['records']['prices']
    changes=price_changes(oldprices,newprices)
    with q.connect(q.TEST_DB) as db:
        current=db.execute("SELECT * FROM market_data_prices WHERE tenant_id=%s AND instrument_id LIKE 'crisis-replay-%%' AND price_date BETWEEN '2008-09-15' AND '2009-03-09' ORDER BY symbol,price_date",(q.TENANT,)).fetchall()
        keyed={(r['symbol'],str(r['price_date'])):r for r in current}
        matched=sum((r['symbol'],str(r['price_date'])) in keyed and all(str(r.get(k))==str(keyed[r['symbol'],str(r['price_date'])].get(k)) for k in ('price','provider','currency','price_type','fetched_at')) for r in newprices)
        if matched!=len(newprices):raise ValueError('Fresh T inputs do not match stored market records')
        dates=['2026-08-27','2026-08-28']
        navs=db.execute("SELECT snapshot_date,currency,total_text FROM subject_nav_series WHERE tenant_id=%s AND subject_id='hong-kong-china' AND snapshot_date=ANY(%s) ORDER BY snapshot_date",(q.TENANT,dates)).fetchall()
        series=b['hong-kong-china']['performance']['series']
        bydate={str(r.get('date',r.get('snapshot_date'))):r for r in series}
        nav_matches=all(str(r['snapshot_date']) in bydate and math.isclose(float(r['total_text']),float(bydate[str(r['snapshot_date'])]['nav']),rel_tol=0,abs_tol=1e-8) for r in navs)
        if len(navs)!=2 or not nav_matches:raise ValueError('Added observations do not reconcile to stored NAV')
        cash=db.execute("SELECT c.account_id,c.snapshot_date,c.currency,c.row_sha256,c.ending_cash_text,c.settled_cash_text FROM cash_snapshots c WHERE c.account_id IN ('U16088571','U16088606') AND c.snapshot_date=ANY(%s) ORDER BY c.account_id,c.snapshot_date,c.currency",(dates,)).fetchall()
        cash_coverage=set();lineage=[]
        for row in cash:
            original=db.execute("SELECT DISTINCT data_json FROM flex_rows WHERE tenant_id=%s AND account_id=%s AND row_sha256=%s AND record_type='CashReportCurrency'",(q.TENANT,row['account_id'],row['row_sha256'])).fetchall()
            attrs=[json.loads(r['data_json']) if isinstance(r['data_json'],str) else r['data_json'] for r in original]
            # Raw values stay inside the private QA execution; retain only match results.
            content_match=bool(attrs) and all(str(r.get('endingCash'))==str(row['ending_cash_text']) and str(r.get('endingSettledCash'))==str(row['settled_cash_text']) for r in attrs)
            cash_coverage.add((row['account_id'],str(row['snapshot_date'])))
            lineage.append({'account':row['account_id'],'date':str(row['snapshot_date']),'currency':row['currency'],'distinct_original_records':len(attrs),'cash_values_match_original':content_match})
    validations={}
    for subject,module in (('alpha-world-ms','alpha_world_ms_crisis'),('alpha-world-t','alpha_world_t_crisis')):
        before=a[subject]['risk']['crisis_replay'];after=b[subject]['risk']['crisis_replay']
        mod=importlib.import_module('ibkr_etl.'+module)
        try:mod.validate(after);valid=True
        except (ValueError,KeyError,TypeError):valid=False
        daily_old={r['observation_date']:r for r in before['daily']};daily_new={r['observation_date']:r for r in after['daily']}
        if set(daily_old)!=set(daily_new):raise ValueError('Crisis calendar changed')
        validations[subject]={'deterministic_recalculation_matches':valid,
            'maximum_normalized_level_change':max(abs(daily_new[d]['normalized_value']-daily_old[d]['normalized_value']) for d in daily_old),
            'maximum_drawdown_change':abs(after['maximum_drawdown']-before['maximum_drawdown']),
            'actual_performance_unchanged':a[subject]['performance']==b[subject]['performance'],
            'holdings_unchanged':a[subject]['holdings']==b[subject]['holdings'],
            'publication_status':after.get('publication_status'),'independent_provider_verification':False}
    us_old=a['U16088571']['holdings']['holdings'];us_new=b['U16088571']['holdings']['holdings']
    labels=[]
    if len(us_old)!=len(us_new):raise ValueError('Physical holdings count changed')
    for x,y in zip(us_old,us_new):
        if x!=y:
            if {k:v for k,v in x.items() if k not in ('symbol','description')}!={k:v for k,v in y.items() if k not in ('symbol','description')}:raise ValueError('Change beyond physical security labels')
            labels.append({'conid':str(y['conid']),'old_symbol':x.get('symbol'),'new_symbol':y.get('symbol'),'only_symbol_description_changed':True})
    print('UPGRADE_INPUT_LINEAGE '+json.dumps({'passed':True,'database_writes':0,'production_connection_used':False,
        'price_records_matched_to_database':matched,'changed_price_series':changes,'crisis_recalculations':validations,
        'hong_kong_added_dates':dates,'added_nav_observations_reconciled':nav_matches,
        'physical_cash_account_date_coverage':len(cash_coverage),'cash_original_record_checks':lineage,
        'security_label_changes':labels,'reports_approved':False,'provider_prices_independently_verified':False}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('UPGRADE_INPUT_LINEAGE_ERROR '+json.dumps({'type':type(error).__name__,'sqlstate':getattr(error,'sqlstate',None)}),flush=True)
        raise SystemExit(1)
