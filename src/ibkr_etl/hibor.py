"""Official HKAB 1M fixing, via HKMA; no Treasury fallback or invented fixings."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import hashlib
import json
import math
import statistics
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

from . import raw_capture
from .market_calendar import HK_BANK_CALENDAR, HKT
from .reference_data import _get

SERIES_ID = 'HKAB-HIBOR-1M'
PROVIDER = 'HKAB HKD Interest Settlement Rates (HKMA/HKAB official endpoints)'
URL = 'https://api.hkma.gov.hk/public/market-data-and-statistics/monthly-statistical-bulletin/er-ir/hk-interbank-ir-daily'
BASIS = 'arithmetic-mean-published-fixings-inception-to-valuation'
# Independently read HKAB /api/hibor?year=2025&month=M&day=D:
# date/1 Month null and ds_is_fb=Y on each date. These are no-publication
# days under Table 1 of HKAB's HIBOR specifications, not stock-exchange
# closures. No subsequent fixing is backdated into the arithmetic average.
NON_PUBLICATION_DATES = frozenset(date.fromisoformat(d) for d in (
    '2025-08-05', '2025-09-08', '2025-09-24'))
FIXING_CALENDAR_VERSION = 'hkab-fixing-calendar-2025-2026-r1'
FIXING_CALENDAR_VERSION_2027 = 'hkab-fixing-calendar-2025-2027-r2'
OWNER_URL = 'https://www.hkab.org.hk/api/hibor'
MAX_OWNER_FALLBACK_DAYS = 32
# Operational cutoff, not an assertion that every fixing publishes at this time:
# 14:30 exceptional publication + 60-minute correction window + 15-minute grace.
PUBLICATION_READY_TIME = time(15, 45)
PUBLICATION_POLICY_VERSION = 'hibor-hkt-1545-correction-grace-v1'


class HiborTransportError(RuntimeError):
    """An official endpoint is unavailable; never expose credentials/HTTP bodies."""

    def __init__(self, message, *, allow_owner_fallback=False):
        super().__init__(message)
        self.allow_owner_fallback = allow_owner_fallback


def expected_fixing_dates(start: date, end: date):
    if end < start:
        raise ValueError('HIBOR interval is reversed')
    days, cursor = [], start
    while cursor <= end:
        if HK_BANK_CALENDAR.is_session(cursor) and cursor not in NON_PUBLICATION_DATES:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def latest_expected_fixing(*, now: datetime):
    """Use HK time; pre-cutoff jobs never request today's unqualified fixing."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('HIBOR publication policy requires an aware timestamp')
    local = now.astimezone(HKT)
    cursor = local.date()
    if local.time().replace(tzinfo=None) < PUBLICATION_READY_TIME:
        cursor -= timedelta(days=1)
    while not HK_BANK_CALENDAR.is_session(cursor) or cursor in NON_PUBLICATION_DATES:
        cursor -= timedelta(days=1)
    return {'expected_date':cursor.isoformat(), 'checked_at':local.isoformat(),
        'publication_ready_time_hkt':PUBLICATION_READY_TIME.isoformat(),
        'publication_policy_version':PUBLICATION_POLICY_VERSION}


def _read_official(getter, url, timeout, *, feed=None, key=None):
    requested_at = raw_capture.utc_now()
    try:
        payload = getter(url, timeout=timeout)
    except HTTPError as error:
        # Invalid requests and denied access need investigation. Availability
        # errors permit one request per date to the other licensed official source.
        if error.code not in {405, 408, 429, 500, 502, 503, 504}:
            raise HiborTransportError('Official HIBOR request rejected HTTP ' + str(error.code)) from None
        raise HiborTransportError('Official HIBOR endpoint unavailable HTTP ' + str(error.code), allow_owner_fallback=True) from None
    except (URLError, TimeoutError, ConnectionError, OSError):
        raise HiborTransportError('Official HIBOR endpoint transport unavailable', allow_owner_fallback=True) from None
    if feed:
        raw_capture.capture_http(feed, PROVIDER, url=url, body=payload, key=key,
                                 extension='json', requested_at=requested_at)
    return payload


def _parse_owner(payload, day):
    response = json.loads(payload) if isinstance(payload, (bytes, str)) else payload
    key = day.isoformat()
    if response.get('ds_is_fb') != 'N':
        raise ValueError('HIBOR fallback/non-publication requires reviewed evidence: ' + key)
    parts = (response.get('year'),response.get('month'),response.get('day'))
    if any(type(value) is not int for value in parts) or parts != (day.year,day.month,day.day):
        raise ValueError('HKAB fixing date does not match requested date: ' + key)
    value = response.get('1 Month')
    if value is None or isinstance(value,bool) or not math.isfinite(float(value)) or not 0<=float(value)<=30:
        raise ValueError('HKAB 1M fixing unavailable or invalid: ' + key)
    return {'rate_date':key, 'annual_rate':float(value)/100}


def parse_page(payload):
    if isinstance(payload, (bytes, str)):
        payload = json.loads(payload)
    if payload.get('header', {}).get('success') is not True:
        raise ValueError('HKMA response is not successful')
    records = payload['result']['records']
    observations = []
    for record in records:
        day = date.fromisoformat(record['end_of_day']).isoformat()
        value = record.get('ir_1m')
        if value is None:
            raise ValueError('Official 1M fixing is missing on ' + day)
        if isinstance(value, bool):
            raise ValueError('Invalid official 1M fixing')
        annual = float(value) / 100.0  # Official field is percent per annum.
        if not math.isfinite(annual) or not 0 <= annual <= .30:
            raise ValueError('Official 1M fixing is outside validation range')
        observations.append({'rate_date': day, 'annual_rate': annual})
    dates = [r['rate_date'] for r in observations]
    if dates != sorted(set(dates), reverse=True):
        raise ValueError('HKMA dates must be unique and descending')
    return observations


def fetch(start: date, end: date, *, getter=_get, timeout=45, cached_observations=None, evidence=None):
    expected = expected_fixing_dates(start, end)  # Fail before network for unverified years.
    expected_keys = {d.isoformat() for d in expected}
    by_date, previous = {}, None
    observed_dates = set()
    transport = {'hkma_requests':0, 'hkab_requests':0, 'hkma_unavailable':None,
        'hkab_dates':[], 'cached_older_fixings':0, 'immediate_retries':0}
    if evidence is not None:
        evidence.update(transport)
    # Stored pass-qualified fixings permit a bounded incremental owner fallback.
    # Re-observe the most recent week; never silently use a cached current fixing.
    seen = set()
    for row in cached_observations or []:
        key = str(row['rate_date'])
        value = row['annual_rate']
        if (key in seen or key not in expected_keys or isinstance(value, bool)
                or not math.isfinite(float(value)) or not 0 <= float(value) <= .30):
            raise ValueError('Invalid cached HIBOR observation: ' + key)
        seen.add(key)
        if date.fromisoformat(key) < end-timedelta(days=7):
            by_date[key] = {'rate_date':key,'annual_rate':float(value)}
    transport['cached_older_fixings']=len(by_date)
    # Do not trust undocumented date filters. Descending pages are verified and
    # filtered locally. Each request happens once; this is pagination, not retry.
    for offset in range(0, 100000, 1000):
        url = URL + '?' + urlencode({'segment':'hibor.fixing', 'pagesize':1000,
            'sortby':'end_of_day', 'sortorder':'desc', 'offset':offset})
        try:
            transport['hkma_requests']+=1
            payload = _read_official(getter, url, timeout, feed='hkma-hibor', key=f'offset-{offset}')
        except HiborTransportError as error:
            if not error.allow_owner_fallback:
                raise
            transport['hkma_unavailable']=str(error)
            break  # No retry of the failing endpoint; use dated HKAB observations.
        page = parse_page(payload)  # Malformed data is NOT a transport fallback.
        if not page:
            break
        if previous is not None and page[0]['rate_date'] >= previous:
            raise ValueError('HKMA pagination did not advance')
        previous = page[-1]['rate_date']
        for row in page:
            if start.isoformat() <= row['rate_date'] <= end.isoformat():
                by_date[row['rate_date']] = row
                observed_dates.add(row['rate_date'])
        if previous < start.isoformat():
            break
    else:
        raise ValueError('HIBOR pagination bound exceeded')
    # HKMA may lag current fixing publication. Query only missing business days
    # directly from the benchmark owner; reject missing/forward/fallback values.
    missing = [d for d in expected if d.isoformat() not in by_date]
    if len(missing) > MAX_OWNER_FALLBACK_DAYS:
        raise ValueError('HIBOR official fallback exceeds 32 dated requests; qualify historical bootstrap first')
    for day in missing:
        official = OWNER_URL + '?' + urlencode({'year':day.year,'month':day.month,'day':day.day})
        transport['hkab_requests']+=1
        by_date[day.isoformat()] = _parse_owner(_read_official(getter,official,timeout,
            feed='hkab-hibor',key=day.isoformat()),day)
        transport['hkab_dates'].append(day.isoformat())
        observed_dates.add(day.isoformat())
    rows = [by_date[k] for k in sorted(by_date)]
    period_average(rows, start, end)
    if evidence is not None:
        transport['observed_dates']=sorted(observed_dates)
        evidence.update(transport)
    return rows


def period_average(observations, start: date, end: date):
    """Equal weight per actual published fixing, inclusive; never forward-fill."""
    if end < start:
        raise ValueError('HIBOR period is reversed')
    rows = [dict(r) for r in observations if start.isoformat() <= str(r['rate_date']) <= end.isoformat()]
    days = [date.fromisoformat(str(r['rate_date'])) for r in rows]
    if not days or days != sorted(set(days)):
        raise ValueError('HIBOR fixing dates are empty, duplicate or unordered')
    expected = expected_fixing_dates(start, end)
    if days != expected:
        missing = sorted(set(expected) - set(days))
        unexpected = sorted(set(days) - set(expected))
        raise ValueError('Incomplete HIBOR fixing coverage: missing=' + str(missing[:8]) + '; unexpected=' + str(unexpected[:8]))
    values = [float(r['annual_rate']) for r in rows]
    if any(isinstance(r['annual_rate'], bool) for r in rows) or not all(math.isfinite(r) and 0 <= r <= .30 for r in values):
        raise ValueError('Invalid HIBOR fixing values')
    canonical = [{'rate_date':d.isoformat(), 'annual_rate':v} for d,v in zip(days,values)]
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return {'series_id':SERIES_ID, 'currency':'HKD', 'tenor':'1-month',
        'annual_rate':statistics.fmean(values), 'rate_date':days[-1].isoformat(),
        'period_start':start.isoformat(), 'period_end':end.isoformat(), 'averaging_basis':BASIS,
        'observations':canonical, 'observation_count':len(rows), 'source_value_sha256':digest,
        'provider':PROVIDER, 'source_url':URL, 'quality_status':'pass',
        'fixing_calendar_version':FIXING_CALENDAR_VERSION if end.year <= 2026 else FIXING_CALENDAR_VERSION_2027,
        'non_publication_dates':[d.isoformat() for d in sorted(NON_PUBLICATION_DATES) if start<=d<=end]}


def load_period_average(connection, start: str, end: str, *, tenant_id=None):
    clause, params = ('tenant_id = ? AND ', (tenant_id,)) if tenant_id else ('', ())
    rows = connection.execute('SELECT rate_date,annual_rate FROM risk_free_rates WHERE '
        + clause + "series_id = ? AND currency = 'HKD' AND tenor = '1-month' AND quality_status = 'pass' "
        + 'AND rate_date BETWEEN ? AND ? ORDER BY rate_date', params + (SERIES_ID,start,end)).fetchall()
    return period_average([dict(r) for r in rows], date.fromisoformat(start), date.fromisoformat(end))


def refresh(connection, start: date, end: date, *, license_scope_confirmed=False):
    if license_scope_confirmed is not True:
        raise ValueError('HIBOR commercial usage rights must be confirmed before ingestion')
    stored=connection.execute("SELECT rate_date,annual_rate FROM risk_free_rates WHERE "
        "series_id = ? AND currency = 'HKD' AND tenor = '1-month' AND quality_status = 'pass' "
        "AND rate_date BETWEEN ? AND ? ORDER BY rate_date", (SERIES_ID,start.isoformat(),end.isoformat())).fetchall()
    transport={}
    observations=fetch(start,end,cached_observations=[dict(row) for row in stored],evidence=transport)
    # Validate independently before the first write, including mocked providers.
    result=period_average(observations,start,end)
    from .util import utc_now_iso
    now=utc_now_iso()
    # Do not falsely stamp retained historical cache entries as fetched today.
    # The complete period is validated, but only actually re-observed dates are
    # upserted. An unchanged fixing can still be newly verified at its endpoint.
    observed=set(transport.get('observed_dates', [r['rate_date'] for r in observations]))
    writes=[r for r in observations if r['rate_date'] in observed]
    try:
        for row in writes:
            connection.execute('''INSERT INTO risk_free_rates(rate_date,series_id,currency,tenor,annual_rate,
                day_count_convention,provider,fetched_at,quality_status)
                VALUES (?,?,'HKD','1-month',?,'annualized-money-market-fixing',?,?,'pass')
                ON CONFLICT(tenant_id,rate_date,series_id) DO UPDATE SET
                    annual_rate=excluded.annual_rate,provider=excluded.provider,fetched_at=excluded.fetched_at,
                    currency=excluded.currency,tenor=excluded.tenor,quality_status=excluded.quality_status''',
                (row['rate_date'],SERIES_ID,row['annual_rate'],PROVIDER,now))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    result.pop('observations')
    result['observations_loaded']=len(writes)
    result['validated_period_observations']=len(observations)
    result['publication_policy_version']=PUBLICATION_POLICY_VERSION
    result['official_transport_fallback_bound']=MAX_OWNER_FALLBACK_DAYS
    result['transport']=transport
    return result
