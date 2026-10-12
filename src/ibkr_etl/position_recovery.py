"""Missing-only recovery of validated, original broker position observations.

No fabricated Flex records, UPDATE or DELETE. The source archive must exist and
its SHA-256 must be verified before this module is called.
"""
from decimal import Decimal
from .util import canonical_json, decimal_text, pick


def normalized_position(row):
    if row.record_type.lower() != 'openposition' or not row.report_date or not row.conid or not row.currency:
        raise ValueError('An original dated OpenPosition with instrument and currency is required')
    a = row.attributes
    result = dict(account_id=row.account_id, snapshot_date=row.report_date,
        conid=row.conid, model_code=pick(a, 'model', 'modelCode'), currency=row.currency,
        quantity_text=decimal_text(pick(a, 'position', 'quantity')),
        cost_basis_text=decimal_text(pick(a, 'costBasisMoney', 'costBasis')),
        mark_price_text=decimal_text(pick(a, 'markPrice', 'closePrice')),
        market_value_text=decimal_text(pick(a, 'positionValue', 'marketValue')),
        unrealized_pnl_text=decimal_text(pick(a, 'fifoPnlUnrealized', 'unrealizedPL')),
        row_sha256=row.row_sha256)
    for key in ('quantity_text', 'mark_price_text', 'market_value_text'):
        if result[key] is None or not Decimal(result[key]).is_finite():
            raise ValueError('A required position value is missing or non-finite')
    for key in ('cost_basis_text', 'unrealized_pnl_text'):
        if result[key] is not None and not Decimal(result[key]).is_finite():
            raise ValueError('An optional broker position value is non-finite')
    return result


def validate_scope(rows, expected):
    """Check complete account/date sets and independently supplied CSV controls."""
    by_key = {}
    for row in rows:
        p = normalized_position(row)
        key = (p['account_id'], p['snapshot_date'], p['conid'], p['model_code'])
        if key in by_key:
            raise ValueError('Duplicate source position identity')
        by_key[key] = (row, p)
    wanted = {(x['account_id'], x['date'], x['conid'], ''): x for x in expected}
    if len(wanted) != len(expected) or set(by_key) != set(wanted):
        raise ValueError('Original Flex account/date/instrument set differs from CSV control')
    for key, (row, p) in by_key.items():
        control = wanted[key]
        if p['currency'] != control['currency']:
            raise ValueError('Currency differs from CSV control')
        for field, source in (('quantity_text', 'quantity'), ('mark_price_text', 'price'), ('market_value_text', 'value')):
            if abs(Decimal(p[field]) - Decimal(control[source])) > Decimal('0.00001'):
                raise ValueError('Original Flex quantity, closing price or valuation differs from CSV')
    return list(by_key.values())


def insert_missing_positions(connection, rows, expected, run_id):
    """Caller owns the atomic transaction, archive registration and postchecks."""
    validated = validate_scope(rows, expected)
    inserted = 0
    for row, p in validated:
        key = (p['account_id'], p['snapshot_date'], p['conid'], p['model_code'])
        existing = connection.execute('''SELECT currency,quantity_text,mark_price_text,market_value_text,
            cost_basis_text,unrealized_pnl_text,row_sha256
            FROM position_snapshots WHERE account_id=? AND snapshot_date=? AND conid=? AND model_code=?''', key).fetchone()
        if existing:
            fields = ('currency', 'quantity_text', 'mark_price_text', 'market_value_text',
                'cost_basis_text', 'unrealized_pnl_text')
            def same_number(old, new):
                return old is None and new is None or old is not None and new is not None and Decimal(old) == Decimal(new)
            if str(existing[0]) != p['currency'] or any(
                not same_number(existing[i], p[fields[i]]) for i in range(1, 6)) or existing[6] != row.row_sha256:
                raise ValueError('Existing broker observation conflicts; it will not be overwritten')
            continue
        if connection.execute('SELECT conid FROM instruments WHERE conid=?', (p['conid'],)).fetchone() is None:
            raise ValueError('Instrument must already exist; recovery cannot alter classifications')
        prior = connection.execute('''SELECT row_sha256 FROM flex_rows
            WHERE account_id=? AND section=? AND record_type=? AND source_key=?''',
            (row.account_id, row.section, row.record_type, row.source_key)).fetchone()
        if prior and prior[0] != row.row_sha256:
            raise ValueError('Conflicting original Flex row; existing evidence preserved')
        connection.execute('''INSERT INTO flex_rows(account_id,section,record_type,source_key,
            report_date,conid,currency,row_sha256,data_json,first_seen_run_id,last_seen_run_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,section,record_type,source_key) DO NOTHING''',
            (row.account_id,row.section,row.record_type,row.source_key,row.report_date,row.conid,
             row.currency,row.row_sha256,canonical_json(row.attributes),run_id,run_id))
        connection.execute('''INSERT INTO position_snapshots(account_id,snapshot_date,conid,model_code,
            currency,quantity_text,cost_basis_text,mark_price_text,market_value_text,unrealized_pnl_text,
            row_sha256,last_seen_run_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            tuple(p[k] for k in ('account_id','snapshot_date','conid','model_code','currency','quantity_text',
                'cost_basis_text','mark_price_text','market_value_text','unrealized_pnl_text','row_sha256')) + (run_id,))
        inserted += 1
    return inserted
