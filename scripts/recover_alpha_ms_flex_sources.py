"""Retrieve only the two authorized one-day sources; never load source tables.

Executed once per date in the existing ingestion job. Original response bytes
are archived create-only before validation. Existing credential circuit and
token-level locking are mandatory, with no request retries or circuit reset.
"""
import json
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / 'src'))
from ibkr_etl.config import load_settings
from ibkr_etl.flex_client import FlexClient
from ibkr_etl.flex_guard import guarded_flex_request
from ibkr_etl.parser import parse_flex_xml
from ibkr_etl.service import _archive_report
from ibkr_etl.util import pick

DAY = os.environ['ALPHA_MS_RECOVERY_DATE']
if DAY not in ('2026-08-27', '2026-08-28'):
    raise ValueError('Recovery date outside approved scope')
ACCOUNTS = {'U16088571': 43, 'U16088606': 33}


def main():
    settings = load_settings()
    with guarded_flex_request(settings):
        response = FlexClient(settings).request_report(date.fromisoformat(DAY), date.fromisoformat(DAY))
    digest, location = _archive_report(settings, response.content, date.fromisoformat(DAY), date.fromisoformat(DAY))
    report = parse_flex_xml(response.content)
    rows = [r for r in report.rows if r.record_type.lower() == 'openposition'
            and r.account_id in ACCOUNTS]
    counts = {a: sum(r.account_id == a and r.report_date == DAY for r in rows) for a in ACCOUNTS}
    if any(r.report_date != DAY for r in rows):
        raise ValueError('Returned positions are not for the requested date; original archived, not imported')
    if counts != ACCOUNTS:
        raise ValueError('Returned holding coverage differs from validated activity statements; not imported')
    for r in rows:
        if not r.conid or not r.currency:
            raise ValueError('Position identity is incomplete')
        for names in (('position', 'quantity'), ('markPrice', 'closePrice'), ('positionValue', 'marketValue')):
            if not Decimal(pick(r.attributes, *names)).is_finite():
                raise ValueError('Non-finite position value')
    print('ALPHA_MS_RECOVERY_RESULT ' + json.dumps({'status': 'original_flex_archived',
        'date': DAY, 'sha256': digest, 'raw_path': location, 'position_counts': counts,
        'bytes': len(response.content), 'source_tables_modified': False,
        'flex_generation_requests': 1}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Never print credentials, request URLs or broker response bodies.
        print('ALPHA_MS_RECOVERY_RESULT ' + json.dumps({'status': 'stopped',
            'date': DAY, 'error_class': type(exc).__name__, 'error_code': getattr(exc, 'code', None),
            'phase': getattr(exc, 'phase', None), 'no_immediate_retry': True,
            'source_tables_modified': False}), flush=True)
        raise SystemExit(1)
