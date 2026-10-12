"""Normalize report holdings and allocations without changing broker evidence."""
from copy import deepcopy
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from .fx_selection import latest_fx_quote


def normalize_collected_report_holdings(connection, subject_id, reporting_currency, tool_results):
    """Read the same account-scoped FX history used by monthly attribution."""
    subject = connection.execute(
        'SELECT subject_type, source_account_id FROM platform_subjects WHERE subject_id = ?',
        (subject_id,),
    ).fetchone()
    if subject is None:
        raise ValueError('Reporting subject is not registered')
    source_account = subject['source_account_id']
    if subject['subject_type'] == 'filtered_portfolio' and not source_account:
        definition = connection.execute(
            "SELECT configuration_json ->> 'source_account_id' AS source_account_id "
            "FROM subject_calculation_definitions WHERE subject_id = ? AND subject_type = 'filtered_portfolio' "
            "AND approval_status IN ('validated', 'approved') ORDER BY definition_version DESC, created_at DESC LIMIT 1",
            (subject_id,),
        ).fetchone()
        source_account = definition['source_account_id'] if definition else None
        if not source_account:
            raise ValueError('Filtered reporting subject has no approved FX source account')
    fx_account = str(source_account or subject_id)
    effective = str(tool_results['holdings']['as_of'])
    rows = connection.execute(
        'SELECT rate_date, from_currency, to_currency, rate_text FROM fx_rates '
        'WHERE account_id = ? AND rate_date <= ? ORDER BY from_currency, to_currency, rate_date',
        (fx_account, effective),
    ).fetchall()
    result = normalize_report_holdings(tool_results, reporting_currency, rows)
    for name in ('holdings', 'sector_allocation'):
        # AgentToolResult uses tuple citations; serialized snapshots use lists.
        # Preserve both inputs while returning JSON-compatible evidence.
        result[name]['citations'] = [*result[name].get('citations', ()),
            {'table':'fx_rates','account_id':fx_account,'as_of':effective,'method':'dated-reporting-fx-v1'}]
    return result


def normalize_report_holdings(tool_results, reporting_currency, fx_rows):
    """Return a copy, preserving native values and dated FX evidence per holding."""
    result = deepcopy(tool_results)
    target = str(reporting_currency).upper()
    holdings = result['holdings']['data']['holdings']
    sector = result['sector_allocation']['data']
    if result['holdings']['data'].get('valuation_method'):
        raise ValueError('Do not normalize an already valued reporting snapshot')
    if sector.get('classification_basis') == 'composite sleeve type':
        if any(r.get('currency') != target for r in holdings):
            raise ValueError('Composite holdings currency differs from reporting currency')
        return result
    history = defaultdict(list)
    for row in fx_rows:
        key = (str(row['from_currency']).upper(), str(row['to_currency']).upper())
        history[key].append((str(row['rate_date']), row['rate_text']))
    for values in history.values():
        values.sort(key=lambda x: x[0])
    def number(value):
        try:
            value = Decimal(str(value))
        except (InvalidOperation, TypeError) as error:
            raise ValueError('Invalid holding value') from error
        if not value.is_finite():
            raise ValueError('Non-finite holding value')
        return value
    by_id = {}
    for row in holdings:
        key = str(row['conid'])
        if key in by_id:
            raise ValueError('Ambiguous duplicate report holding')
        native = str(row['currency']).upper()
        if len(native) != 3 or not native.isalpha():
            raise ValueError('Unknown holding currency')
        day = date.fromisoformat(str(row['snapshot_date'])).isoformat()
        if day > str(result['holdings']['as_of']):
            raise ValueError('Future-dated holding')
        if native == target:
            rate, observed, direction = Decimal(1), day, 'identity'
        else:
            quote = latest_fx_quote(history.get((native, target), []), day)
            direction = 'direct'
            if quote is None:
                quote = latest_fx_quote(history.get((target, native), []), day)
                direction = 'reciprocal'
            if quote is None:
                raise ValueError('Missing dated reporting FX for holding')
            rate, observed = Decimal(str(quote[0])), quote[1]
            if direction == 'reciprocal':
                rate = Decimal(1) / rate
        value = number(row['market_value_text']) * rate
        row.update(native_currency=native, native_market_value_text=row['market_value_text'],
                   currency=target, market_value_text=str(value),
                   reporting_fx={'from':native,'to':target,'rate':str(rate),'date':observed,'direction':direction})
        if row.get('mark_price_text') not in (None, ''):
            row['native_mark_price_text'] = row['mark_price_text']
            row['mark_price_text'] = str(number(row['mark_price_text']) * rate)
        by_id[key] = abs(value)
    gross = sum(by_id.values(), Decimal(0))
    totals, details, counts, detail_counts = defaultdict(Decimal), defaultdict(Decimal), defaultdict(int), defaultdict(int)
    classified = Decimal(0)
    seen = set()
    for row in sector.get('classifications', []):
        key = str(row['conid'])
        if key not in by_id or key in seen:
            raise ValueError('Classification does not match unique report holdings')
        seen.add(key)
        value = by_id[key]
        classified += value
        row.update(market_value=float(value), weight=float(value/gross) if gross else None)
        totals[row['sector']] += value
        counts[row['sector']] += 1
        detail = (row['region'], row['sector'], row['sub_sector'])
        details[detail] += value
        detail_counts[detail] += 1
    if sector.get('available') and seen != set(by_id):
        raise ValueError('Available classification omits report holdings')
    sector.update(gross_market_value=float(gross), classified_market_value=float(classified),
                  classification_coverage_pct=float(100*classified/gross) if gross else 0.0,
                  currency=target, valuation_method='dated-reporting-fx-v1')
    sector['allocation'] = [dict(sector=k,market_value=float(v),weight=float(v/gross) if gross else None,holdings=counts[k])
                            for k,v in sorted(totals.items(),key=lambda x:x[1],reverse=True)]
    sector['allocation_detail'] = [dict(region=k[0],sector=k[1],sub_sector=k[2],market_value=float(v),
                                      weight=float(v/gross) if gross else None,holdings=detail_counts[k])
                                   for k,v in sorted(details.items(),key=lambda x:x[1],reverse=True)]
    result['holdings']['data'].update(currency=target,valuation_method='dated-reporting-fx-v1')
    holdings.sort(key=lambda r:abs(Decimal(r['market_value_text'])),reverse=True)
    sector['classifications'].sort(key=lambda r:r['market_value'],reverse=True)
    return result
