"""Independent read-only numerical review; writes only a new audit receipt."""
from collections import Counter, defaultdict
from datetime import date, timedelta, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ibkr_etl.market_calendar import CALENDARS
from ibkr_etl.alpha_world_ms_analytics import analyze, simulate, VERSION
from ibkr_etl.alpha_world_ms_pdf_validation import validate
from ibkr_etl.risk_snapshots import snapshot_read_status, canonical_json, RISK_ANALYTICS_VERSION, CALCULATION_VERSION


def main():
    folder = ROOT / '.work/alpha-ms-hkd-hibor-20261007'
    source = json.loads((folder / 'source-inspection.json').read_bytes())
    history = json.loads((folder / 'hkd-history.json').read_bytes())
    fixings = json.loads((folder / 'official-hibor.json').read_bytes())
    start = history['series'][0]['date']
    preferred = ['U16088571'] + list(dict.fromkeys(r['account_id'] for r in source['broker_fx'] if r['account_id'] != 'U16088571'))

    def quote(currency, day):
        if currency == 'HKD':
            return 1., day
        for account in preferred:
            for inverse in (False, True):
                pair = ('HKD', currency) if inverse else (currency, 'HKD')
                choices = [r for r in source['broker_fx'] if r['account_id'] == account and
                    (r['from_currency'], r['to_currency']) == pair and r['rate_date'] <= day]
                if choices:
                    row = max(choices, key=lambda r:r['rate_date'])
                    return (1 / float(row['rate_text']) if inverse else float(row['rate_text'])), row['rate_date']
        raise ValueError('No independent FX observation')

    components = {(r['snapshot_date'],r['component_id']):r for r in source['components']}
    reconstructed = []
    quote_ages = Counter()
    checks = 0
    for nav in history['series']:
        total = float(history['contract']['constant_cash'])
        for spec in history['contract']['components']:
            original = components[start,spec['component_id']]
            current = components[nav['date'],spec['component_id']]
            rate, fixing_date = quote(spec['source_currency'],nav['date'])
            initial_rate, _ = quote(spec['source_currency'],start)
            contribution = 100*float(spec['coefficient'])*float(current['source_value'])*rate/(float(original['source_value'])*initial_rate)
            total += contribution
            checks += 1
            quote_ages[(date.fromisoformat(nav['date'])-date.fromisoformat(fixing_date)).days] += 1
        assert math.isclose(total,nav['nav'],rel_tol=0,abs_tol=1e-10)
        reconstructed.append(total)

    monthly = []
    for path in sorted((folder/'hkd-inputs').glob('*.json')):
        snapshot = json.loads(path.read_bytes())
        rows = snapshot['performance']['series']
        dates = [date.fromisoformat(r['date']) for r in rows]
        levels = np.asarray([r['nav'] for r in rows],dtype=float)
        daily = []
        exclusions = []
        for i in range(1,len(dates)):
            between = [dates[i-1]+timedelta(days=k) for k in range(1,(dates[i]-dates[i-1]).days)]
            if any(CALENDARS['XHKG'].is_session(d) or CALENDARS['XNYS'].is_session(d) for d in between):
                exclusions.append((dates[i-1].isoformat(),dates[i].isoformat()))
            else:
                daily.append(levels[i]/levels[i-1]-1)
        returns = np.asarray(daily)
        valid_rates = [r['annual_rate'] for r in fixings if dates[0].isoformat() <= r['rate_date'] <= dates[-1].isoformat()]
        rate = float(np.mean(valid_rates))
        sd = float(np.std(returns,ddof=1))
        threshold = float(np.quantile(returns,.05,method='linear'))
        independent = {
            'total_return':float(levels[-1]/levels[0]-1),
            'annualized_return':float((levels[-1]/levels[0])**(365.25/(dates[-1]-dates[0]).days)-1),
            'annualized_volatility':sd*math.sqrt(252),
            'sharpe_ratio':(float(np.mean(returns))-math.expm1(math.log1p(rate)/252))/sd*math.sqrt(252),
            'maximum_drawdown':float(np.min(levels/np.maximum.accumulate(levels)-1)),
            'var_95_daily':max(0.,-threshold),
            'cvar_95_daily':max(0.,-float(np.mean(returns[returns<=threshold]))),
            'win_rate':float(np.count_nonzero(returns>0)/len(returns)),
            'risk_free_annual_rate':rate,
        }
        actual, actual_daily, evidence = analyze(snapshot)
        for key,value in independent.items():
            assert math.isclose(value,actual[key],rel_tol=0,abs_tol=1e-11), (path.name,key)
            assert math.isclose(value,snapshot['risk']['metrics'][key],rel_tol=0,abs_tol=1e-11)
        assert np.allclose(returns,actual_daily,rtol=0,atol=1e-14)
        assert len(exclusions) == len(evidence['excluded_intervals'])
        rng = random.Random(int(hashlib.sha256(f"alpha-world-ms|{snapshot['period_end']}|{VERSION}".encode()).hexdigest()[:16],16))
        indices = np.asarray([[rng.randrange(len(returns)) for _ in range(252)] for _ in range(1000)])
        independent_paths = np.prod(1+returns[indices],axis=1)-1
        actual_paths, mc_mean = simulate(actual_daily,snapshot['period_end'])
        assert np.allclose(independent_paths,actual_paths,rtol=0,atol=1e-12)
        monthly.append({'period_end':snapshot['period_end'],'valuation_date':snapshot['data_as_of'],
            'nav_observations':len(levels),'daily_returns':len(returns),'excluded_intervals':exclusions,
            'fixings':len(valid_rates),'metrics':independent,'mc_mean':mc_mean,'simulation_paths_verified':1000})

    september = json.loads((folder/'hkd-inputs/2026-09-30.json').read_bytes())
    pdf = ROOT/'output/pdf/AlphaWorld_MS_Azure_HKD_HIBOR_v14r8/AlphaWorld_MS_Factsheet_2026-09-30.pdf'
    geometry = validate(pdf,september)
    manifest = json.loads((pdf.parent/'manifest.json').read_bytes())
    hashes = []
    for entry in manifest['artifacts']:
        file = pdf.parent/Path(entry['blob_name']).name
        assert hashlib.sha256(file.read_bytes()).hexdigest() == entry['sha256']
        expected = next(m for m in monthly if m['period_end']==entry['period_end'])
        for key,value in expected['metrics'].items():
            assert math.isclose(value,entry['render']['chart_calculation']['metrics'][key],rel_tol=0,abs_tol=1e-11)
        hashes.append({'period_end':entry['period_end'],'sha256':entry['sha256']})
    legacy = {'account_id':'alpha-world-ms','as_of':'2026-09-30','analytics_version':RISK_ANALYTICS_VERSION,
        'benchmark_id':'sp500-price','reporting_currency':'USD','metrics':{'sharpe_ratio':0.1},'quality_status':'pass',
        'risk_free':{'currency':'USD','series_id':'treasury-as-of'}}
    stored = {'payload_json':legacy,'payload_sha256':hashlib.sha256(canonical_json(legacy).encode()).hexdigest(),
        'comparison_version':CALCULATION_VERSION,'quality_status':'pass'}
    old_cache_status = snapshot_read_status(stored,subject_id='alpha-world-ms',as_of='2026-09-30')
    result = {'captured_at':datetime.now(timezone.utc).isoformat(),'read_only':True,
        'source_capture_sha256':hashlib.sha256((folder/'source-inspection.json').read_bytes()).hexdigest(),
        'nav_dates_verified':len(reconstructed),'component_contributions_verified':checks,'fx_age_days':dict(quote_ages),
        'periods_verified':len(monthly),'simulation_paths_verified':sum(r['simulation_paths_verified'] for r in monthly),
        'monthly_results':monthly,'september_pdf_geometry':geometry,'azure_pdf_integrity':hashes,
        'legacy_usd_non_hibor_cache_fault_probe':old_cache_status,
        'limitations':['Source component arithmetic is checked against the captured database, not independently certified against every original Flex file.',
            'Market-calendar interval selection is independent code but shares the approved calendar definitions.',
            'Financial assumptions and live/private methodology divergence require separate review.']}
    target = ROOT/'.work/alpha-ms-calculation-audit-20261007/independent-numerical-review.json'
    with target.open('x',encoding='utf-8') as handle:
        json.dump(result,handle,indent=2,allow_nan=False)
    print(json.dumps({k:v for k,v in result.items() if k not in ('monthly_results','azure_pdf_integrity')},indent=2))


if __name__ == '__main__':
    main()
