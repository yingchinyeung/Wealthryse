"""Read-only Decimal reconciliation of the approved mid-period flow convention.

Reads received transfers and dated IBKR FX without editing either. Derived
ledger checks prove projection arithmetic, not the completeness of its sources.
No ingestion, dispatch, report approval or new financial methodology.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext
import json

from .comparison_source_evidence import fingerprint, number, series
from .util import canonical_json, sha256_text

VERSION = 'comparison-cashflow-observation-v1'


def transfer_day(value):
    text=str(value or '').strip()
    if len(text)==8 and text.isdigit():
        text=f'{text[:4]}-{text[4:6]}-{text[6:]}'
    elif len(text)>=10 and text[4:5]=='-':
        text=text[:10]
    return date.fromisoformat(text)


def external_flows(evidence, *, tenant_id, subject_id, base_currency, as_of):
    if (str(evidence['tenant_id'])!=str(tenant_id) or evidence['subject_id']!=subject_id
            or evidence['base_currency']!=base_currency or evidence['as_of']!=as_of):
        raise ValueError('Cash-flow evidence scope mismatch')
    cutoff=date.fromisoformat(as_of)
    flows=defaultdict(Decimal)
    seen=set()
    kind=evidence['source_kind']
    if kind=='received_transfers':
        rates=defaultdict(list)
        for row in evidence['fx']:
            if row['account_id']!=subject_id or str(row['tenant_id'])!=str(tenant_id):
                raise ValueError('Transfer FX account mismatch')
            day=date.fromisoformat(str(row['rate_date']))
            if day<=cutoff:
                rates[(row['from_currency'],row['to_currency'])].append((day,row['rate_text']))
        def rate(currency,day):
            if currency==base_currency:return Decimal(1)
            eligible=[r for r in rates[(currency,base_currency)] if r[0]<=day]
            if not eligible:raise ValueError('Missing non-forward-looking transfer FX')
            latest=max(r[0] for r in eligible)
            values=[r[1] for r in eligible if r[0]==latest]
            if len(values)!=1:raise ValueError('Ambiguous latest transfer FX')
            return number(values[0],positive=True)
        for row in evidence['transfers']:
            if (row['account_id']!=subject_id or str(row['tenant_id'])!=str(tenant_id)
                    or row['section']!='Transfers'):
                raise ValueError('Transfer account/section mismatch')
            key=(row['section'],row['record_type'],row['source_key'])
            if key in seen:raise ValueError('Duplicate transfer source identity')
            seen.add(key)
            values=json.loads(row['data_json']) if isinstance(row['data_json'],str) else row['data_json']
            if sha256_text(canonical_json(values))!=row['row_sha256']:
                raise ValueError('Received transfer hash mismatch')
            day=transfer_day(values.get('date') or values.get('reportDate'))
            if day>cutoff:continue
            if str(values.get('assetCategory') or '').upper()=='CASH':
                amount=number(values.get('cashTransfer'))*rate(values.get('currency'),day)
            elif values.get('positionAmountInBase') not in (None,''):
                amount=number(values['positionAmountInBase'])
            else:
                amount=number(values.get('positionAmount'))*rate(values.get('currency'),day)
            flows[day]+=amount
    elif kind=='derived_external_ledger':
        for row in evidence['ledger']:
            if row['portfolio_id']!=subject_id or str(row['tenant_id'])!=str(tenant_id):
                raise ValueError('Derived cash-flow account mismatch')
            if row['event_key'] in seen:raise ValueError('Duplicate derived event identity')
            seen.add(row['event_key'])
            day=date.fromisoformat(str(row['event_date']))
            if row['included'] not in (True,1) or row['external_flow'] not in (True,1):
                raise ValueError('Non-external ledger row supplied as external cash flow')
            if day<=cutoff:flows[day]+=number(row['amount_base'])
    else:
        raise ValueError('Unreviewed cash-flow source kind')
    return dict(flows)


def verify_returns(data, evidence):
    contract=data['contract']
    expected_kind={'physical_account':'received_transfers','filtered_portfolio':'derived_external_ledger',
        'composite_portfolio':'derived_external_ledger'}.get(contract['subject_type'])
    if expected_kind is None or evidence['source_kind']!=expected_kind:
        raise ValueError('Cash-flow source does not match subject contract')
    if contract['base_currency']!=contract['reporting_currency']:
        raise ValueError('Unreviewed portfolio currency conversion')
    with localcontext() as context:
        context.prec=40
        flows=external_flows(evidence,tenant_id=contract['tenant_id'],subject_id=contract['subject_id'],
            base_currency=contract['base_currency'],as_of=data['as_of'])
        nav=series(data['nav'],'snapshot_date','total_text')
        prices=series(data['prices'],'observation_date','level')
        start=date.fromisoformat(str(contract['history_start_date']))
        cutoff=date.fromisoformat(data['as_of'])
        days=sorted(day for day in nav.keys() & prices.keys() if start<=day<=cutoff)
        saved={}
        for row in data['comparisons']:
            if (str(row['tenant_id'])!=str(contract['tenant_id']) or row['subject_id']!=contract['subject_id']
                    or row['benchmark_id']!=contract['benchmark_id']
                    or row['reporting_currency']!=contract['reporting_currency']
                    or row['calculation_version']!='benchmark-comparison-v1'):
                raise ValueError('Comparison cash-flow identity mismatch')
            day=date.fromisoformat(str(row['observation_date']))
            if day in saved:raise ValueError('Duplicate comparison date')
            if row['quality_status'] not in ('pass','warn'):
                raise ValueError('Failed comparison cannot certify cash-flow projection')
            saved[day]=row
        issues=[]
        if len(days)<2:issues.append({'code':'insufficient_return_history','date':None})
        if set(saved)!=set(days):issues.append({'code':'comparison_date_inventory_mismatch','date':None})
        growth=Decimal(100)
        checked=0
        def check(row,key,expected,day):
            actual=row.get(key)
            tolerance=max(Decimal('0.00000001'),abs(expected)*Decimal('0.0000000001'))
            if actual is None or abs(number(actual)-expected)>tolerance:
                issues.append({'code':key+'_cashflow_mismatch','date':day.isoformat()})
        for previous,day in zip(days,days[1:]):
            row=saved.get(day)
            if row is None:continue
            flow=sum((value for flow_day,value in flows.items() if previous<flow_day<=day),Decimal(0))
            denominator=nav[previous]+Decimal('0.5')*flow
            if denominator<=0:
                issues.append({'code':'nonpositive_cashflow_denominator','date':day.isoformat()});continue
            result=(nav[day]-nav[previous]-flow)/denominator
            if result<=-1:
                issues.append({'code':'invalid_cashflow_return','date':day.isoformat()});continue
            check(row,'portfolio_return',result,day)
            growth*=1+result
            check(row,'normalized_portfolio',growth,day)
            checked+=1
        counts={}
        for issue in issues:counts[issue['code']]=counts.get(issue['code'],0)+1
        return {'observation_version':VERSION,'subject_id':contract['subject_id'],
            'benchmark_id':contract['benchmark_id'],'as_of':data['as_of'],
            'source_kind':evidence['source_kind'],'cashflow_projection_verified':not issues,
            'return_observations_checked':checked,'flow_source_sha256':fingerprint(evidence),
            'issue_counts':counts,'issue_samples':issues[:10],
            'methodology':'existing modified-Dietz daily/common-date, half-period flow weighting',
            'source_completeness_verified':False,'execution_authorized':False,
            'warning':'Received-transfer/derived-ledger projection is not proof of complete IBKR ingestion or derived-source lineage'}


def load_flow_evidence(connection, data):
    context=connection.execute("SELECT app_tenant_id() AS tenant_id, current_setting('transaction_read_only') AS read_only, current_setting('transaction_isolation') AS isolation").fetchone()
    contract=data['contract'];tenant_id=contract['tenant_id'];subject_id=contract['subject_id']
    if (str(context['tenant_id'])!=str(tenant_id) or context['read_only']!='on'
            or context['isolation'] not in ('repeatable read','serializable')):
        raise ValueError('Tenant-scoped consistent READ ONLY transaction required')
    subject=connection.execute('SELECT subject_type FROM platform_subjects WHERE tenant_id=? AND subject_id=?',
        (tenant_id,subject_id)).fetchone()
    if subject is None:raise ValueError('Unknown financial subject')
    record={'tenant_id':str(tenant_id),'subject_id':subject_id,'base_currency':contract['base_currency'],'as_of':data['as_of']}
    def rows(sql,args):return [dict(row) for row in connection.execute(sql,args).fetchall()]
    if subject['subject_type']=='physical_account':
        record.update(source_kind='received_transfers',transfers=rows('''SELECT tenant_id,account_id,section,record_type,source_key,row_sha256,data_json
            FROM flex_rows WHERE tenant_id=? AND account_id=? AND section='Transfers' ORDER BY record_type,source_key''',(tenant_id,subject_id)),
            fx=rows('''SELECT a.tenant_id,f.account_id,f.rate_date,f.from_currency,f.to_currency,f.rate_text
            FROM fx_rates f JOIN accounts a ON a.account_id=f.account_id
            WHERE a.tenant_id=? AND f.account_id=? AND f.rate_date<=?
            ORDER BY f.from_currency,f.to_currency,f.rate_date''',
                (tenant_id,subject_id,data['as_of'])))
    elif subject['subject_type'] in ('filtered_portfolio','composite_portfolio'):
        record.update(source_kind='derived_external_ledger',ledger=rows('''SELECT tenant_id,portfolio_id,event_key,event_date,amount_base,included,external_flow
            FROM derived_portfolio_cash_ledger WHERE tenant_id=? AND portfolio_id=? AND included=1 AND external_flow=1
            AND event_date<=? ORDER BY event_date,event_key''',(tenant_id,subject_id,data['as_of'])))
    else:
        raise ValueError('Unsupported financial subject type')
    return record
