"""Scoped, atomic correction of disposable derived history; broker facts immutable.

Preview runs the real calculation and reporting collection, then rolls back.
Apply retains existing financial values, adds the recovered dates, and exports
new private payloads. It never supersedes frozen reporting snapshots or changes
approvals, release status, onboarding, market observations or broker records.
"""
import base64
import hashlib
import json
import math
import os
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path[:0] = ['/app/src', '/app/scripts']
import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl.derived_portfolio import refresh_filtered_portfolio, refresh_fixed_unit_composite
from ibkr_etl.subject_onboarding import validate_typed_runtime_contract
from ibkr_etl.provisional_benchmark import mapped_benchmarks, _external_flows, _portfolio_values
from ibkr_etl.benchmark_analytics import BenchmarkContract, build_comparison
from ibkr_etl.reporting_snapshot import _collect_tool_results, assemble_month_end_payload
from ibkr_etl.alpha_world_ms_analytics import analyze
from ibkr_etl.util import canonical_json

TENANT = '00000000-0000-0000-0000-000000000001'
SUBJECTS = ('hong-kong-china', 'alpha-world-ms')
DAYS = ('2026-08-27', '2026-08-28')
PERIODS = ('2026-08-31', '2026-09-30')
MODE = os.environ['ALPHA_MS_RECOVERY_MODE']
if MODE not in ('inspect', 'preview', 'apply'):
    raise ValueError('Explicit scoped recovery mode required')
PREFIX = 'review/alpha-world-ms/source-recovery-20261007/'
LABEL = os.environ.get('ALPHA_MS_RECOVERY_AUDIT_LABEL', MODE)
if LABEL not in ('inspect','preview','apply','comparison-preview','comparison-apply'):
    raise ValueError('Recovery audit label outside registered scope')
PHASE = 'start'


class DeferredConnection(PostgresConnection):
    """Internal helper commits cannot escape the outer recovery transaction."""
    def commit(self):
        pass

    def rollback(self):
        # The helper re-raises its original failure; outer raw context rolls back.
        # Preserve that exception for diagnosis instead of masking it here.
        pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(c):
    names = ('accounts', 'instruments', 'nav_snapshots', 'position_snapshots',
        'cash_snapshots', 'trades', 'cash_transactions', 'fx_rates', 'flex_rows',
        'corporate_actions', 'fx_transactions', 'raw_reports', 'benchmark_prices',
        'reference_fx_rates', 'risk_free_rates', 'market_data_prices',
        'platform_subjects', 'subject_calculation_definitions', 'reporting_snapshots',
        'internal_report_artifacts', 'account_onboarding_cases', 'account_onboarding_approvals')
    result = {}
    for name in names:
        if c.execute('SELECT to_regclass(?)', ('public.' + name,)).fetchone()[0] is None:
            continue
        h = hashlib.sha256()
        count = 0
        for r in c.execute('SELECT to_jsonb(t)::text FROM ' + name + ' t ORDER BY to_jsonb(t)::text'):
            h.update(r[0].encode()); h.update(b'\n'); count += 1
        result[name] = {'rows': count, 'sha256': h.hexdigest()}
    return result


def nav_values(c, subject):
    return {(str(r['snapshot_date']), r['currency']): float(r['total_nav'])
        for r in c.execute('SELECT snapshot_date,currency,total_nav FROM derived_portfolio_nav WHERE portfolio_id=?', (subject,))}


def close(a, b):
    return math.isfinite(a) and math.isfinite(b) and abs(a-b) <= max(1e-8, abs(b)*1e-12)


def rebuild_comparisons(c, subject):
    """Preserve existing converted levels; fill only actual recovered-date closes."""
    records = []
    for spec in mapped_benchmarks(c, subject):
        existing = {str(r['observation_date']): dict(r) for r in c.execute(
            'SELECT * FROM performance_comparison_daily WHERE subject_id=? AND benchmark_id=? AND calculation_version=? ORDER BY observation_date',
            (subject, spec.benchmark_id, 'benchmark-comparison-v1'))}
        if not existing:
            raise ValueError('Existing approved comparison basis unavailable: ' + subject + '/' + spec.benchmark_id)
        levels = {d: float(r['benchmark_level']) for d, r in existing.items()}
        earliest = min(existing)
        values = _portfolio_values(c, subject, earliest)
        added = []
        # Also qualify already-stored NAV dates newer than the last comparison.
        # A missing actual close on a required session remains a hard failure;
        # a closed exchange does not justify synthesizing another observation.
        fill_days = sorted(set(DAYS) | {d for d in values if d > max(existing)})
        for day in fill_days:
            if day in levels:
                continue
            prices = c.execute('''SELECT level,currency,observation_status,provider FROM benchmark_prices
                WHERE benchmark_id=? AND observation_date=? AND quality_status IN ('pass','warn')
                AND observation_status IN ('official_close','provisional_close','corrected')''', (spec.benchmark_id, day)).fetchall()
            if not prices and day not in DAYS:
                from datetime import date
                from ibkr_etl.market_calendar import benchmark_session_on_or_before
                required = benchmark_session_on_or_before(spec.benchmark_id, date.fromisoformat(day))
                if required is not None and required.isoformat() < day:
                    continue
            if len(prices) != 1:
                raise ValueError('Unambiguous actual benchmark close required: ' + spec.benchmark_id + '/' + day)
            price = prices[0]
            if price['currency'] != spec.currency:
                raise ValueError('Benchmark native currency differs from approved contract')
            rate = 1.0
            if spec.currency != spec.reporting_currency:
                # Exact-date approved stored FX only. No synthesized market values.
                fx = c.execute('''SELECT rate,provider FROM reference_fx_rates WHERE rate_date=?
                    AND from_currency=? AND to_currency=? AND quality_status IN ('pass','warn')''',
                    (day, spec.currency, spec.reporting_currency)).fetchall()
                if len(fx) != 1:
                    raise ValueError('Unambiguous exact-date reporting FX required: ' + spec.benchmark_id + '/' + day)
                rate = float(fx[0]['rate'])
            levels[day] = float(price['level']) * rate
            added.append({'date': day, 'level': levels[day], 'native_currency': spec.currency, 'fx_rate': rate})
        result = build_comparison(subject_id=subject, reporting_currency=spec.reporting_currency,
            portfolio_currency=spec.reporting_currency, portfolio_values=values,
            cash_flows=_external_flows(c, subject),
            benchmark=BenchmarkContract(spec.benchmark_id, spec.reporting_currency, spec.return_convention,
                'provisional_close', 'restricted'), benchmark_levels=levels,
            start_date=earliest, end_date=max(values), expected_as_of=max(values),
            development_mode=True, required_return_convention=spec.return_convention)
        if result.quality_status == 'fail':
            raise ValueError('Rebuilt benchmark comparison fails financial validation: ' + spec.benchmark_id)
        now = datetime.now(timezone.utc).isoformat()
        count = 0
        for row in result.rows:
            old = existing.get(row.observation_date)
            if row.observation_date < DAYS[0]:
                if not old:
                    raise ValueError('Unexpected earlier comparison date')
                for field in ('portfolio_value', 'benchmark_level', 'normalized_portfolio', 'normalized_benchmark'):
                    if not close(float(getattr(row, field)), float(old[field])):
                        raise ValueError('Earlier financial comparison differs: ' + subject + '/' + spec.benchmark_id + '/' + field)
                continue
            c.execute('''INSERT INTO performance_comparison_daily(subject_id,benchmark_id,observation_date,
                reporting_currency,portfolio_value,benchmark_level,normalized_portfolio,normalized_benchmark,
                portfolio_return,benchmark_return,excess_return,calculation_version,quality_status,generated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(tenant_id,subject_id,benchmark_id,observation_date,calculation_version)
                DO UPDATE SET portfolio_value=excluded.portfolio_value,benchmark_level=excluded.benchmark_level,
                normalized_portfolio=excluded.normalized_portfolio,normalized_benchmark=excluded.normalized_benchmark,
                portfolio_return=excluded.portfolio_return,benchmark_return=excluded.benchmark_return,
                excess_return=excluded.excess_return,quality_status=excluded.quality_status,generated_at=excluded.generated_at''',
                (subject, spec.benchmark_id, row.observation_date, spec.reporting_currency,
                row.portfolio_value, row.benchmark_level, row.normalized_portfolio, row.normalized_benchmark,
                row.portfolio_return, row.benchmark_return, row.excess_return, result.calculation_version,
                result.quality_status, now))
            count += 1
        if not set(DAYS).issubset({r.observation_date for r in result.rows}):
            raise ValueError('Recovered dates absent from rebuilt comparison')
        records.append({'benchmark_id': spec.benchmark_id, 'rows_rebuilt': count,
            'recovered_closes': added, 'quality_status': result.quality_status})
    return records


def main():
    global PHASE
    payloads = {}
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=_hybrid_row_factory,
            connect_timeout=25, options='-c statement_timeout=180000 -c lock_timeout=15000') as raw:
        c = DeferredConnection(raw)
        c.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
        c.execute("SELECT set_config('app.tenant_id',?,true)", (TENANT,))
        for name in ('ibkr-daily:', 'self-service-derived:'):
            if not c.execute('SELECT pg_try_advisory_xact_lock(hashtext(?))', (name + TENANT,)).fetchone()[0]:
                raise ValueError('Scheduled processing busy; no overlapping recovery allowed')
        PHASE = 'read_contracts'
        definitions = []
        for subject in SUBJECTS:
            rows = c.execute('''SELECT d.*,s.subject_type FROM subject_calculation_definitions d
                JOIN platform_subjects s ON s.tenant_id=d.tenant_id AND s.subject_id=d.subject_id
                WHERE d.subject_id=? AND d.approval_status='approved' AND d.effective_to IS NULL''', (subject,)).fetchall()
            if len(rows) != 1:
                raise ValueError('Exactly one owner-approved calculation required: ' + subject)
            row = rows[0]
            contract = validate_typed_runtime_contract(subject, row['subject_type'],
                row['configuration_json'], row['configuration_sha256'])
            definitions.append((row, contract))
        for account, expected in (('U16088571', 43), ('U16088606', 33)):
            for day in DAYS:
                if c.execute('SELECT COUNT(*) FROM position_snapshots WHERE account_id=? AND snapshot_date=?', (account, day)).fetchone()[0] != expected:
                    raise ValueError('Original position recovery incomplete')
        before_nav = {s: nav_values(c, s) for s in SUBJECTS}
        PHASE = 'protect_source_and_published_history'
        before = fingerprint(c)
        contracts = [{'subject_id': r['subject_id'], 'sha256': r['configuration_sha256']} for r, _ in definitions]
        if MODE == 'inspect':
            result = {'status': 'inspected', 'mode': MODE, 'contracts': contracts,
                'derived_recovered_dates': {s: {d: before_nav[s].get((d, 'USD')) for d in DAYS} for s in SUBJECTS}}
            raw.rollback()
        else:
            reference = zlib.decompress(base64.b64decode(REFERENCE_BYTES))
            if sha(reference) != REFERENCE_SHA256:
                raise ValueError('Published reference fingerprint differs')
            calculations = []
            with TemporaryDirectory(prefix='alpha-ms-recovery-') as directory:
                reference_path = Path(directory) / 'reference_data.json'
                reference_path.write_bytes(reference)
                for row, contract in definitions:
                    subject = row['subject_id']
                    PHASE = 'calculate_' + subject
                    if row['subject_type'] == 'filtered_portfolio':
                        # The qualified deployed calculation predates the optional
                        # hash parameter. The caller already verifies these exact
                        # published bytes above; do not replace its financial method.
                        calculated = refresh_filtered_portfolio(c, reference_path, contract=contract)
                    else:
                        calculated = refresh_fixed_unit_composite(c, contract)
                    after_nav = nav_values(c, subject)
                    for key, value in before_nav[subject].items():
                        if key not in after_nav or not close(after_nav[key], value):
                            raise ValueError('Previously calculated NAV unexpectedly changed: ' + subject + '/' + key[0])
                    if any((d, 'USD') not in after_nav for d in DAYS):
                        raise ValueError('Recovered dates missing from derived calculation')
                    added = sorted(set(after_nav) - set(before_nav[subject]))
                    if any(key[0] not in DAYS for key in added):
                        raise ValueError('Unexpected unscoped new derived dates')
                    PHASE = 'comparisons_' + subject
                    comparisons = rebuild_comparisons(c, subject)
                    calculations.append({'subject_id': subject, 'existing_nav_values_unchanged': True,
                        'new_observations': [{'date': d, 'currency': currency, 'nav': after_nav[(d,currency)]} for d,currency in added],
                        'recovered_nav': {d: after_nav[(d,'USD')] for d in DAYS}, 'comparisons': comparisons,
                        'calculation': calculated})
            reports = []
            for period in PERIODS:
                PHASE = 'reporting_payload_' + period
                tool_results = _collect_tool_results(c, tenant_id=TENANT, subject_id='alpha-world-ms',
                    period_end=period, reporting_currency='USD')
                payload, quality, allowed, blockers = assemble_month_end_payload(subject_id='alpha-world-ms',
                    period_start='2025-01-20', period_end=period, reporting_currency='USD', tool_results=tool_results)
                if quality == 'fail':
                    raise ValueError('Corrected reporting payload failed validation: ' + period)
                metrics, returns, evidence = analyze(payload)
                if any(set(DAYS).intersection(x['missing_sessions']) for x in evidence['excluded_intervals']):
                    raise ValueError('Recovered dates still excluded from reporting analytics')
                parents = [dict(r) for r in c.execute('''SELECT snapshot_id::text,data_sha256,status FROM reporting_snapshots
                    WHERE subject_id=? AND period_end=? AND status IN ('frozen','approved')''', ('alpha-world-ms', period))]
                data = canonical_json(payload).encode()
                if LABEL.startswith('comparison-') and sha(data) != EXPECTED_REPORT_HASHES.get(period):
                    raise ValueError('Latest-date comparison unexpectedly changes an already corrected report input')
                payloads[period] = data
                reports.append({'period_end': period, 'data_sha256': sha(data), 'parent_snapshots': parents,
                    'quality_status': quality, 'publication_allowed': allowed, 'publication_blockers': list(blockers),
                    'metrics': metrics, 'analytics_evidence': evidence})
            PHASE = 'verify_immutable_inputs'
            after = fingerprint(c)
            if before != after:
                raise ValueError('Protected broker, approved contract or published snapshot changed; refusing entire transaction')
            result = {'status': 'rollback_validated' if MODE == 'preview' else 'derived_history_committed',
                'mode': MODE, 'contracts': contracts, 'reference_sha256': REFERENCE_SHA256,
                'calculations': calculations, 'reports': reports,
                'broker_facts_unchanged': True, 'published_snapshots_unchanged': True,
                'reports_released': False, 'onboarding_activated': False,
                'protected_fingerprints': before}
            if MODE == 'preview':
                raw.rollback()
            else:
                raw.commit()
    PHASE = 'archive_private_evidence'
    service = BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    def put(name, data):
        blob = service.get_blob_client('internal-reports', PREFIX + name)
        if blob.exists():
            if blob.download_blob().readall() != data:
                raise ValueError('Existing immutable private input differs')
        else:
            blob.upload_blob(data, overwrite=False, content_settings=ContentSettings(content_type='application/json'), metadata={'sha256': sha(data)})
        if sha(blob.download_blob().readall()) != sha(data):
            raise ValueError('Private evidence readback differs')
    if MODE == 'apply':
        for period, data in payloads.items():
            put('corrected-inputs/' + period + '.json', data)
    put('derived-' + LABEL + '.json', json.dumps(result, sort_keys=True, indent=2, default=str).encode())
    print('ALPHA_MS_RECOVERY_RESULT ' + json.dumps({k:v for k,v in result.items()
        if k not in ('protected_fingerprints','calculations','reports')}, default=str), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        import traceback
        print('ALPHA_MS_RECOVERY_RESULT ' + json.dumps({'status':'stopped','mode':MODE,'phase':PHASE,
            'error_class':type(error).__name__, 'error':str(error) if isinstance(error,(ValueError,TypeError,AttributeError,KeyError))
            else 'Database or service details retained privately; credentials redacted',
            'frames': [{'file':Path(f.filename).name,'function':f.name,'line':f.lineno}
                for f in traceback.extract_tb(error.__traceback__)]}), flush=True)
        raise SystemExit(1)
