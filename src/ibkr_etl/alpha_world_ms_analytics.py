"""Versioned MS factsheet analytics from immutable composite NAV observations.

No database/source writes. NAV-based growth uses every observed valuation;
daily risk/bootstrap statistics exclude intervals spanning missing joint-market
sessions rather than inventing prices or treating a multi-day change as daily.
"""
from __future__ import annotations
from datetime import date, timedelta
import hashlib
import json
import math
import random
import statistics
from .market_calendar import CALENDARS, CALENDAR_VERSION

VERSION = "alpha-world-ms-factsheet-hkd-hibor-v3"
SESSIONS_PER_YEAR = 252
PATHS = 1000


def analyze(snapshot):
    if snapshot.get("subject_id") != "alpha-world-ms":
        raise ValueError("MS analytics cannot be used for another subject")
    return _analyze(snapshot, subject_id='alpha-world-ms', version=VERSION,
                    markets=('XNYS', 'XHKG'))


def _analyze(snapshot, *, subject_id, version, markets):
    """Shared arithmetic; callers must enforce their own subject/methodology contract."""
    period = date.fromisoformat(snapshot["period_end"])
    rows = snapshot["performance"]["series"]
    dates = [date.fromisoformat(r["date"]) for r in rows]
    nav = [float(r["nav"]) for r in rows]
    if len(nav) < 2 or not all(math.isfinite(v) and v > 0 for v in nav):
        raise ValueError("MS needs at least two finite positive NAV observations")
    if any(a >= b for a,b in zip(dates,dates[1:])) or dates[-1] > period:
        raise ValueError("MS NAV dates must be unique, ordered and not future-dated")
    if dates[0].isoformat() != snapshot.get("period_start",dates[0].isoformat()):
        raise ValueError("MS inception observation is missing; do not silently rebase")
    if dates[-1].isoformat() != snapshot["data_as_of"]:
        raise ValueError("MS last valuation does not match data_as_of")
    rate_data = snapshot.get("risk",{}).get("risk_free",{})
    if snapshot.get('reporting_currency') == 'HKD':
        from .hibor import period_average
        verified = period_average(rate_data.get('observations', []), dates[0], dates[-1])
        for field in ('series_id','currency','tenor','annual_rate','period_start','period_end',
                      'averaging_basis','source_value_sha256','observation_count','fixing_calendar_version'):
            if rate_data.get(field) != verified[field]:
                raise ValueError('MS HKD risk-free period evidence differs: ' + field)
        if any(row.get('currency') != 'HKD' for row in rows):
            raise ValueError('MS HKD NAV contains mixed or missing currencies')
    rate = rate_data.get("annual_rate",snapshot.get("risk",{}).get("metrics",{}).get("risk_free_annual_rate"))
    if rate is None or not math.isfinite(float(rate)) or float(rate)<=-1:
        raise ValueError("MS needs a validated finite as-of risk-free yield")
    rate=float(rate)
    if rate_data.get("rate_date") and date.fromisoformat(rate_data["rate_date"]) > dates[-1]:
        raise ValueError("MS risk-free observation is later than the valuation")
    daily, excluded = [], []
    for before,after,old,new in zip(dates,dates[1:],nav,nav[1:]):
        missing=[]
        cursor=before+timedelta(days=1)
        while cursor < after:
            if any(CALENDARS[market].is_session(cursor) for market in markets):
                missing.append(cursor.isoformat())
            cursor+=timedelta(days=1)
        if missing:
            excluded.append({"from":before.isoformat(),"to":after.isoformat(),"missing_sessions":missing})
        else:
            daily.append(new/old-1)
    if len(daily)<2:
        raise ValueError("MS requires at least two validated one-session returns; source dates need attention")
    rf_daily=(1+rate)**(1/SESSIONS_PER_YEAR)-1
    sigma=statistics.stdev(daily)
    ordered=sorted(daily)
    index=(len(ordered)-1)*.05
    lower=int(index)
    threshold=ordered[lower]+(ordered[min(lower+1,len(ordered)-1)]-ordered[lower])*(index-lower)
    tail=[r for r in daily if r <= threshold]
    peak=nav[0]
    drawdown=0.
    for v in nav:
        peak=max(peak,v)
        drawdown=min(drawdown,v/peak-1)
    growth=nav[-1]/nav[0]
    metrics={"total_return":growth-1,"annualized_return":growth**(365.25/(dates[-1]-dates[0]).days)-1,
             "annualized_volatility":sigma*math.sqrt(SESSIONS_PER_YEAR),
             "sharpe_ratio":(statistics.fmean(daily)-rf_daily)/sigma*math.sqrt(SESSIONS_PER_YEAR) if sigma>0 else None,
             "maximum_drawdown":drawdown,"var_95_daily":max(0.,-threshold),
             "cvar_95_daily":max(0.,-statistics.fmean(tail)),"win_rate":sum(r>0 for r in daily)/len(daily),
             "risk_free_annual_rate":rate}
    canonical=json.dumps({"subject":subject_id,"currency":snapshot["reporting_currency"],
                          "dates":[d.isoformat() for d in dates],"nav":nav,"rate":rate,
                          "risk_free_source_value_sha256":rate_data.get('source_value_sha256'),
                          "risk_free_averaging_basis":rate_data.get('averaging_basis')},sort_keys=True,allow_nan=False)
    evidence={"version":version,"start":dates[0].isoformat(),"end":dates[-1].isoformat(),
              "nav_observations":len(nav),"daily_return_observations":len(daily),"excluded_intervals":excluded,
              "source_value_sha256":hashlib.sha256(canonical.encode()).hexdigest(),"calendar_version":CALENDAR_VERSION,
              "growth_annualization":"365.25/calendar days","risk_annualization":"252 validated session returns/year",
              "simulation_horizon_sessions":SESSIONS_PER_YEAR,"simulation_paths":PATHS,
              "simulation_interest":"Actual effects present in NAV only; no projected financing or cash interest",
              "short_history":len(daily)<SESSIONS_PER_YEAR,"risk_free_rate_date":rate_data.get("rate_date"),
              "risk_free_averaging_basis":rate_data.get('averaging_basis'),
              "risk_free_source_value_sha256":rate_data.get('source_value_sha256'),
              "reporting_currency":snapshot['reporting_currency'],
              "source_data_changed":False}
    return metrics,tuple(daily),evidence


def simulate(daily_returns, period_end, *, paths=PATHS):
    return _simulate(daily_returns, period_end, paths=paths,
                     subject_id='alpha-world-ms', version=VERSION)


def _simulate(daily_returns, period_end, *, paths, subject_id, version):
    if not daily_returns or paths<1:
        raise ValueError("A non-empty validated daily sample and positive path count are required")
    seed=int(hashlib.sha256(f"{subject_id}|{period_end}|{version}".encode()).hexdigest()[:16],16)
    rng=random.Random(seed)
    sample=[]
    for _ in range(paths):
        wealth=math.prod(1+daily_returns[rng.randrange(len(daily_returns))] for _ in range(SESSIONS_PER_YEAR))
        if not math.isfinite(wealth) or wealth<=0:
            raise ValueError("MS simulation produced an invalid wealth path")
        sample.append(wealth-1)
    return sample,statistics.fmean(sample)
