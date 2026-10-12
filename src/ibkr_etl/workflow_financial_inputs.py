"""Read-only value manifests for stored physical-account financial inputs.

Preservation and fingerprints do not prove source completeness or reconcile a
derived calculation. No writes, provider requests, raw-file reads or dispatch.
"""
from datetime import date, datetime
from decimal import Decimal, DecimalException
from uuid import UUID
import json
import re
from .workflow_lineage import _readonly, _day, inspect_lineage
from .workflow_ledger import digest
from .util import canonical_json, sha256_text, pick

VERSION='workflow-financial-input-observation-v1'
MAX_ROWS=100000
# Closed inventory: table, observation date, identity, exact consumed/provenance fields, numeric fields.
TABLES={
    'position_snapshots':('snapshot_date',('snapshot_date','conid','model_code'),
        'snapshot_date conid model_code currency quantity_text cost_basis_text mark_price_text market_value_text unrealized_pnl_text row_sha256 last_seen_run_id',
        'quantity_text cost_basis_text mark_price_text market_value_text unrealized_pnl_text'),
    'nav_snapshots':('snapshot_date',('snapshot_date','currency','asset_class'),
        'snapshot_date currency asset_class total_text cash_text stock_text options_text futures_text bonds_text accruals_text row_sha256 last_seen_run_id',
        'total_text cash_text stock_text options_text futures_text bonds_text accruals_text'),
    'cash_snapshots':('snapshot_date',('snapshot_date','currency'),
        'snapshot_date currency ending_cash_text settled_cash_text fx_rate_to_base_text row_sha256 last_seen_run_id',
        'ending_cash_text settled_cash_text fx_rate_to_base_text'),
    'cash_transactions':('effective_date',('source_key',),
        'source_key transaction_id event_datetime model_code effective_date settle_date transaction_type description amount_text currency conid row_sha256 last_seen_run_id',
        'amount_text'),
    'trades':('trade_date',('source_key',),
        'source_key conid trade_date settle_date buy_sell quantity_text proceeds_text commission_text commission_currency net_cash_text currency row_sha256 last_seen_run_id',
        'quantity_text proceeds_text commission_text net_cash_text'),
    'fx_rates':('rate_date',('rate_date','from_currency','to_currency'),
        'rate_date from_currency to_currency rate_text row_sha256 last_seen_run_id','rate_text'),
    'corporate_actions':('effective_date',('source_key',),
        'source_key action_id effective_date action_type description quantity_text amount_text currency conid row_sha256 last_seen_run_id',
        'quantity_text amount_text'),
    'fx_transactions':('report_date',('source_key',),
        'source_key event_datetime activity_description model_code report_date functional_currency fx_currency quantity_text proceeds_text cost_text realized_pnl_text row_sha256 last_seen_run_id',
        'quantity_text proceeds_text cost_text realized_pnl_text'),
}


def _scalar(value):
    if isinstance(value,(UUID,date,datetime,Decimal)):return str(value)
    if isinstance(value,float):return str(value)  # Includes unusable values as data, not JSON NaN.
    if value is None or type(value) in (str,int,bool):return value
    raise ValueError('Unsupported stored input scalar')


def observe_bundle(bundle):
    """Validate scope/identity and fingerprint values without silently dropping defects."""
    tenant=str(UUID(str(bundle['tenant_id'])));account=bundle['account_id']
    start,cutoff=_day(bundle['start']),_day(bundle['as_of'])
    if start>cutoff or set(bundle['tables'])!=set(TABLES):raise ValueError('Incomplete bounded input inventory')
    issues={};manifests={};conids=set();run_ids=set()
    def issue(code):issues[code]=issues.get(code,0)+1
    def scope(row):
        if str(row['tenant_id'])!=tenant or row['account_id']!=account:
            raise ValueError('Cross-account or cross-tenant financial input')
    def finite(value,code,positive=False):
        if value in (None,''):return
        try:
            number=Decimal(str(value))
            valid=number.is_finite() and (not positive or number>0)
        except DecimalException:valid=False
        if not valid:issue(code)
    for table,(day_field,keys,columns,numeric) in TABLES.items():
        rows=bundle['tables'][table]
        if len(rows)>MAX_ROWS:raise ValueError('Financial input row limit exceeded')
        identities=set();values=[];days=set()
        for raw in rows:
            row=dict(raw);scope(row)
            identity=tuple(row[k] for k in keys)
            if any(v is None or (v=='' and k!='model_code') for k,v in zip(keys,identity)) or identity in identities:
                raise ValueError('Missing or duplicate normalized input identity')
            identities.add(identity)
            if row[day_field] in (None,''):issue(table+':undated')
            else:
                day=_day(row[day_field])
                if day>cutoff or (table!='fx_rates' and day<start):raise ValueError('Financial input outside cutoff')
                days.add(day)
            for field in numeric.split():finite(row[field],table+':'+field+':unusable',
                positive=table=='fx_rates' or field=='fx_rate_to_base_text')
            required={'position_snapshots':('quantity_text','market_value_text'),'cash_transactions':('amount_text',),
                'cash_snapshots':('ending_cash_text',),'trades':('quantity_text',),'fx_rates':('rate_text',)}.get(table,())
            if table=='nav_snapshots' and row['asset_class']=='TOTAL':required=('total_text',)
            for field in required:
                if row[field] in (None,''):issue(table+':'+field+':missing')
            if not re.fullmatch('[0-9a-f]{64}',str(row['row_sha256'] or '')):issue(table+':invalid_source_hash')
            if type(row['last_seen_run_id']) is not int or row['last_seen_run_id']<=0:
                raise ValueError('Invalid normalized ingestion identity')
            if row.get('conid'):conids.add(row['conid'])
            run_ids.add(row['last_seen_run_id'])
            values.append({k:_scalar(row[k]) for k in ('tenant_id','account_id',*columns.split())})
        values.sort(key=lambda r:json.dumps(r,sort_keys=True))
        manifests[table]={'rows':len(values),'through_date':max(days).isoformat() if days else None,
            'sha256':digest({'tenant_id':tenant,'account_id':account,'table':table,'rows':values})}
    instruments={}
    for raw in bundle['instruments']:
        row=dict(raw)
        if row['conid'] not in conids or row['conid'] in instruments:raise ValueError('Unexpected instrument input')
        instruments[row['conid']]={k:_scalar(v) for k,v in row.items()}
    if set(instruments)!=conids:issue('instrument_metadata_missing')
    parsed=[];seen=set()
    for raw in bundle['parsed_rows']:
        row=dict(raw);scope(row)
        identity=tuple(row[k] for k in ('section','record_type','source_key'))
        if identity in seen:raise ValueError('Duplicate parsed input identity')
        seen.add(identity)
        if row['report_date'] and not start<=_day(row['report_date'])<=cutoff:
            raise ValueError('Parsed input outside cutoff')
        attrs=json.loads(row['data_json'])
        if not isinstance(attrs,dict) or any(type(k) is not str or type(v) is not str for k,v in attrs.items()):
            raise ValueError('Invalid parsed input attributes')
        if pick(attrs,'accountId','account') not in ('','-','UNKNOWN',account):raise ValueError('Parsed account mismatch')
        if sha256_text(canonical_json(attrs))!=row['row_sha256']:issue('parsed_input_content_hash_mismatch')
        run_ids.update((row['first_seen_run_id'],row['last_seen_run_id']))
        parsed.append({k:_scalar(v) for k,v in row.items()})
    runs={}
    for raw in bundle['runs']:
        row=dict(raw)
        if str(row['tenant_id'])!=tenant or row['run_id'] not in run_ids or row['run_id'] in runs:
            raise ValueError('Unexpected or cross-tenant ingestion lineage')
        runs[row['run_id']]={k:_scalar(v) for k,v in row.items()}
        if (row['status']!='success' or not row['completed_at']
                or not re.fullmatch('[0-9a-f]{64}',str(row['raw_sha256'] or ''))):
            issue('ingestion_lineage_not_successful')
    if set(runs)!=run_ids:issue('ingestion_lineage_missing')
    result={'version':VERSION,'tenant_id':tenant,'account_id':account,'start':start.isoformat(),
        'as_of':cutoff.isoformat(),'tables':manifests,'instruments':{'rows':len(instruments),'sha256':digest(instruments)},
        'parsed_rows':{'rows':len(parsed),'sha256':digest(sorted(parsed,key=lambda r:json.dumps(r,sort_keys=True)))},
        'ingestion_runs':{'rows':len(runs),'sha256':digest(sorted(runs.values(),key=lambda r:r['run_id']))},
        'issue_counts':issues,'stored_input_integrity_verified':not issues,
        'reference_cache_verified':False,'raw_xml_verified':False,'source_completeness_verified':False,
        'all_consumed_inputs_verified':False,'execution_authorized':False}
    result['input_manifest_sha256']=digest(result)
    return result


def load_bundle(connection,*,tenant_id,account_id,start,as_of):
    _readonly(connection,tenant_id)
    start=_day(start).isoformat();as_of=_day(as_of).isoformat()
    if start>as_of:raise ValueError('Invalid input date range')
    def rows(sql,args):
        values=[dict(r) for r in connection.execute(sql,args).fetchall()]
        if len(values)>MAX_ROWS:raise ValueError('Financial input row limit exceeded')
        return values
    owners=rows('SELECT tenant_id,account_id FROM accounts WHERE tenant_id=? AND account_id=?',(tenant_id,account_id))
    if len(owners)!=1:raise ValueError('Physical account ownership missing or ambiguous')
    tables={}
    for table,(day_field,keys,columns,_) in TABLES.items():
        # FX may carry a prior quote from before the reporting start. Undated rows stay visible.
        predicate=f'(t.{day_field} IS NULL OR t.{day_field}<=?)' if table=='fx_rates' else (
            f'(t.{day_field} IS NULL OR t.{day_field}=\'\' OR t.{day_field} BETWEEN ? AND ?)')
        args=(tenant_id,account_id,as_of,MAX_ROWS+1) if table=='fx_rates' else (tenant_id,account_id,start,as_of,MAX_ROWS+1)
        tables[table]=rows(f'''SELECT a.tenant_id,t.account_id,{','.join('t.'+c for c in columns.split())}
            FROM {table} t JOIN accounts a ON a.account_id=t.account_id
            WHERE a.tenant_id=? AND t.account_id=? AND {predicate}
            ORDER BY {','.join('t.'+k for k in keys)} LIMIT ?''',args)
    conids=sorted({r['conid'] for values in tables.values() for r in values if r.get('conid')})
    if len(conids)>10000:raise ValueError('Instrument identity limit exceeded')
    instruments=[]
    if conids:
        instruments=rows(f'''SELECT conid,symbol,description,asset_category,currency,listing_exchange,source_json
            FROM instruments WHERE conid IN ({','.join('?' for _ in conids)}) ORDER BY conid LIMIT ?''',(*conids,MAX_ROWS+1))
    parsed=rows('''SELECT tenant_id,account_id,section,record_type,source_key,report_date,conid,currency,
        row_sha256,data_json,first_seen_run_id,last_seen_run_id FROM flex_rows WHERE tenant_id=? AND account_id=?
        AND (report_date IS NULL OR report_date='' OR report_date BETWEEN ? AND ?)
        ORDER BY section,record_type,source_key LIMIT ?''',(tenant_id,account_id,start,as_of,MAX_ROWS+1))
    run_ids=sorted({r['last_seen_run_id'] for values in tables.values() for r in values}|
        {r[k] for r in parsed for k in ('first_seen_run_id','last_seen_run_id')})
    if len(run_ids)>10000:raise ValueError('Ingestion identity limit exceeded')
    runs=[]
    if run_ids:
        runs=rows(f'''SELECT tenant_id,run_id,run_type,requested_from,requested_to,status,started_at,completed_at,raw_sha256
            FROM ingestion_runs WHERE tenant_id=? AND run_id IN ({','.join('?' for _ in run_ids)})
            ORDER BY run_id LIMIT ?''',(tenant_id,*run_ids,MAX_ROWS+1))
    return {'tenant_id':tenant_id,'account_id':account_id,'start':start,'as_of':as_of,
            'tables':tables,'instruments':instruments,'parsed_rows':parsed,'runs':runs}


def inspect_financial_inputs(connection,*,tenant_id,as_of):
    lineage=inspect_lineage(connection,tenant_id=tenant_id,as_of=as_of)
    plan_hash=lineage['graph_sha256'];inputs={}
    subjects=connection.execute('''SELECT subject_id,history_start_date FROM platform_subjects
        WHERE tenant_id=? AND lifecycle_status='active' AND subject_type='physical_account'
        AND history_start_date<=? ORDER BY subject_id''',(tenant_id,as_of)).fetchall()
    for row in subjects:
        if row['subject_id'] not in lineage['order']:raise ValueError('Physical input registry drift')
        inputs[row['subject_id']]=observe_bundle(load_bundle(connection,tenant_id=tenant_id,
            account_id=row['subject_id'],start=row['history_start_date'],as_of=as_of))
    hashes={}
    for subject in lineage['order']:
        parents=[{'kind':e['kind'],'parent_id':e['parent_id'],'sha256':hashes[e['parent_id']]}
            for e in lineage['edges'] if e['subject_id']==subject and e['kind']!='benchmark']
        hashes[subject]=digest({'version':VERSION,'graph_sha256':plan_hash,'subject_id':subject,
            'nav_lineage_sha256':lineage['root_lineage_sha256'].get(subject),
            'physical_inputs':inputs.get(subject),'parents':parents})
    return {'version':VERSION,'as_of':as_of,'database_read_only':True,'lineage':lineage,
        'physical_input_manifests':inputs,'financial_lineage_sha256':hashes,
        'stored_input_integrity_verified':all(r['stored_input_integrity_verified'] for r in inputs.values()),
        'raw_xml_verified':False,'source_completeness_verified':False,'all_consumed_inputs_verified':False,
        'execution_authorized':False}
