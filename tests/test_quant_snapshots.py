"""The gold quant sections (ibkr_etl.quant_snapshots) on synthetic inputs, without a database.

Every builder runs on made-up rows shaped as load_inputs() returns them: known answers where the
engines give one (equal weights, contributions that add up, a hand-worked order), the unavailable
paths, the fingerprint, the build setting and JSON-ready payloads. tests/test_quant_snapshots_db.py
covers the migration, the loaders, the job and the reader on a real PostgreSQL.
"""
from __future__ import annotations

import importlib.util
import json
import math
import random
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from ibkr_etl import quant_snapshots as qs

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "U9100001"
AS_OF = "2026-10-16"


def business_days(end: str, count: int) -> list[str]:
    days, day = [], date.fromisoformat(end)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    return sorted(days)


def synthetic_data(*, holdings: int = 8, days: int = 320, seed: int = 7, as_of: str = AS_OF,
                   subject_type: str = "physical_account", trade_every: int = 7) -> dict:
    """Rows for one made-up account: USD and HKD holdings driven by one market factor, a trade
    every few days, one deposit, the benchmark comparison and the HKD rate. Deterministic."""
    rng = random.Random(seed)
    calendar = business_days(as_of, days)
    conids = [str(9001 + i) for i in range(holdings)]
    currency = {conid: ("HKD" if i % 3 == 2 else "USD") for i, conid in enumerate(conids)}
    beta = {conid: 0.6 + 0.8 * i / max(1, holdings - 1) for i, conid in enumerate(conids)}
    price = {conid: (40.0 + 7 * i) * (7.8 if currency[conid] == "HKD" else 1.0) for i, conid in enumerate(conids)}
    quantity = {conid: 100.0 + 10 * i for i, conid in enumerate(conids)}
    hkd = 0.1282
    cash, level = 20000.0, 100.0
    data = {"subject_id": ACCOUNT, "as_of": as_of, "subject_type": subject_type, "reporting_currency": "USD",
            "benchmark_id": "synthetic-benchmark", "comparison": [], "positions": [], "trades": [], "nav": [],
            "flows": [], "fx": [], "risk_free": {"rate_date": as_of, "annual_rate": 0.04},
            "classifications": {conid: ("Technology", "Financials", "Energy")[i % 3]
                                for i, conid in enumerate(conids[:-1])}}
    previous_nav = None
    for t, day in enumerate(calendar):
        # Calm and stressed spells of 80 sessions, so the returns have regimes to find.
        market = rng.gauss(0.0003, 0.006 if (t // 80) % 2 == 0 else 0.02) if t else 0.0
        hkd *= 1.0 + (rng.gauss(0.0, 0.0005) if t else 0.0)
        data["fx"].append({"date": day, "from": "HKD", "to": "USD", "rate": hkd})
        for conid in conids:
            if t:
                price[conid] *= 1.0 + beta[conid] * market + rng.gauss(0.0, 0.012)
        level *= 1.0 + market
        flow = 0.0
        if t == days // 3:
            flow = 5000.0
            cash += flow
            data["flows"].append({"date": day, "amount": "5000", "currency": "USD"})
        if t and t % trade_every == 0:
            conid = conids[t % holdings]
            side = "BUY" if (t // trade_every) % 2 else "SELL"
            fill = price[conid] * (1.0005 if side == "BUY" else 0.9995)
            size = 10.0
            rate = hkd if currency[conid] == "HKD" else 1.0
            quantity[conid] += size if side == "BUY" else -size
            cash -= (size if side == "BUY" else -size) * fill * rate + 1.0
            data["trades"].append({"trade_id": f"T{t}", "execution_id": f"E{t}.1", "trade_date": day,
                                   "conid": conid, "symbol": f"SYN{conid[-1]}", "side": side,
                                   "quantity": str(size if side == "BUY" else -size), "price": f"{fill:.6f}",
                                   "commission": "-1.0", "commission_currency": "USD", "currency": currency[conid]})
        nav = cash
        for conid in conids:
            rate = hkd if currency[conid] == "HKD" else 1.0
            value = quantity[conid] * price[conid]
            nav += value * rate
            data["positions"].append({"date": day, "conid": conid, "symbol": f"SYN{conid[-1]}",
                                      "currency": currency[conid], "quantity": f"{quantity[conid]:.4f}",
                                      "mark": f"{price[conid]:.6f}", "market_value": f"{value:.4f}"})
        data["nav"].append({"date": day, "nav": f"{nav:.4f}"})
        data["comparison"].append({
            "date": day, "portfolio_value": nav, "benchmark_level": level,
            "portfolio_return": None if previous_nav is None else (nav - flow) / previous_nav - 1.0,
            "benchmark_return": None if t == 0 else market})
        previous_nav = nav
    return data


# ---------------------------------------------------------------- phase 3 silver rows

def _shift_day(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def _isin(country: str, body: str) -> str:
    from ibkr_etl.sec_13f import isin_check_digit

    return f"{country}{body}{isin_check_digit(country + body)}"


FACTOR_COLUMNS = ("Mkt-RF", "SMB", "HML", "RMW", "CMA", "MOM", "RF")
MACRO_UNITS = {"DGS10": "percent", "DGS2": "percent", "DTWEXBGS": "index", "DCOILBRENTEU": "usd_per_barrel",
               "VIXCLS": "index", "USDCNH": "cnh_per_usd"}
FILERS = (  # cik, name: the 13F filers of synthetic_market()
    ("0000100001", "Alpha Capital"), ("0000100002", "Beta Partners"), ("0000100003", "Gamma Fund"),
    ("0000100004", "Gamma Fund"), ("0000100005", "Delta Advisors"), ("0000100006", "Echo Management"),
    ("0000100007", "Hotel Holdings"))


def synthetic_market(data: dict, *, seed: int = 5, vix: bool = False) -> dict:
    """Rows of the four phase 3 silver tables for synthetic_data()'s account (phase3-schemas.md),
    plus the ISINs its instruments carry. Deterministic. The cases the readers must get right:

    * daily_volumes: each holding's volumes under its conid, as the holdings' own fetch stores
      them; the last holding has none; a benchmark's chart rows (a market_data_prices instrument
      with the first holding's symbol) that must never be read for a holding; a few days are
      missing (not stored) and a few are zero (holidays, skipped).
    * factor_returns: developed factors whose market factor is the account's benchmark return
      less RF; dates in the last month have an older vintage with wrong values (the newer one
      counts, holding only the rows it changed); the last fortnight only as ETF stand-ins
      (left out); a us region that must not be read.
    * macro_series: the series daily over three years, a few days missing, and a newer vintage
      revising the last fortnight. VIXCLS only with vix=True: by default the loader leaves it out
      (Cboe copyright, kept only when MACRO_SERIES_LICENSE_CONFIRMED=VIXCLS).
    * institutional_holdings: an amendment (the later accession counts), one filed later under a
      lower accession (the newest filed_at counts, not the highest accession), a quarter filed after
      the date (not known yet), an older quarter, two filers with one name, an option and a
      principal amount (not stakes), a zero value, a fund holding none of our names, one holding
      exactly our US names in our proportions (similarity 1) and one filed on the date itself.
    """
    from ibkr_etl.sec_13f import cusip_from_isin

    rng = random.Random(seed)
    as_of = data["as_of"]
    last = max(row["date"] for row in data["positions"] if row["date"] <= as_of)
    held = {row["conid"]: row for row in data["positions"] if row["date"] == last}
    conids = sorted(held)
    instruments = {}
    for conid in conids:
        row = held[conid]
        if conid == conids[-1]:
            isin = None
        elif row["currency"] == "HKD":
            isin = _isin("HK", f"{conid}00000"[:9])
        else:
            isin = _isin("US", f"S{conid:0<8}")
        instruments[conid] = {"symbol": row["symbol"], "listing_exchange": "", "currency": row["currency"],
                              "isin": isin, "asset_category": "STK"}

    # Volumes, for the 60 days to the date, under each holding's conid (the holdings' own fetch).
    days = [day for day in business_days(as_of, 50) if day >= _shift_day(as_of, -60)]
    prices, volumes = [], []
    for index, conid in enumerate(conids[:-1]):  # the last holding has no volumes of its own
        base = 150.0 if index == 1 else 40_000.0 * (1 + index)  # the second holding is slow to sell
        for day in days:
            draw = rng.random()
            if draw < 0.05:
                continue
            volumes.append({"instrument_id": str(conid), "price_date": day,
                            "volume": 0.0 if draw > 0.97 else float(round(base * rng.uniform(0.5, 1.5)))})
    # A benchmark's chart rows, under its market_data_prices id with the first holding's symbol:
    # never a holding's volumes, which are read by conid only.
    first = instruments[conids[0]]["symbol"]
    prices.append({"instrument_id": f"yahoo:{first}", "symbol": first})
    volumes += [{"instrument_id": f"yahoo:{first}", "price_date": day, "volume": 7.0} for day in days[-5:]]

    # Factor returns, from before 1 January and the year before the date.
    start = _shift_day(min(f"{as_of[:4]}-01-01", _shift_day(as_of, -365)), -14)
    old_vintage, new_vintage = _shift_day(as_of, -16), _shift_day(as_of, -1)
    market = {row["date"]: row["benchmark_return"] for row in data["comparison"]
              if row["benchmark_return"] is not None}
    rf = 0.04 / 252
    factors = []
    for day in business_days(as_of, 420):
        if day < start:
            continue
        values = {"Mkt-RF": market.get(day, rng.gauss(0.0, 0.01)) - rf, "SMB": rng.gauss(0.0, 0.004),
                  "HML": rng.gauss(0.0, 0.004), "RMW": rng.gauss(0.0, 0.003), "CMA": rng.gauss(0.0, 0.003),
                  "MOM": rng.gauss(0.0, 0.006), "RF": rf}
        for factor in FACTOR_COLUMNS:
            value = values[factor]
            old = {"region": "developed", "factor": factor, "return_date": day, "source": "kenneth-french",
                   "vintage": old_vintage, "fetched_at": f"{old_vintage}T06:00:00Z"}
            new = {**old, "vintage": new_vintage, "fetched_at": f"{new_vintage}T06:00:00Z"}
            if day > old_vintage:  # published since the older file: fund stand-ins until the next one
                factors.append({**new, "value": value, "source": "etf-proxy"})
            elif day > _shift_day(as_of, -30):  # revised in the newer file
                factors += [{**old, "value": value + 0.01}, {**new, "value": value}]
            else:
                factors.append({**old, "value": value})
                if day.endswith("-15"):
                    factors.append({**old, "region": "us", "value": 9.0})

    # Macro levels over three years and a little more.
    level = {"DGS10": 4.0, "DGS2": 3.6, "DTWEXBGS": 120.0, "DCOILBRENTEU": 80.0, "VIXCLS": 18.0, "USDCNH": 7.2}
    macro = []
    for day in business_days(as_of, 820):
        if day < _shift_day(as_of, -365 * 3 - 21):
            continue
        move = market.get(day, rng.gauss(0.0, 0.008))
        level["DGS10"] = max(0.5, level["DGS10"] + 2.0 * move + rng.gauss(0.0, 0.04))
        level["DGS2"] = max(0.3, level["DGS2"] + rng.gauss(0.0, 0.04))
        level["DTWEXBGS"] *= math.exp(-0.3 * move + rng.gauss(0.0, 0.003))
        level["DCOILBRENTEU"] *= math.exp(0.8 * move + rng.gauss(0.0, 0.015))
        level["VIXCLS"] = max(9.0, level["VIXCLS"] * math.exp(-3.0 * move + rng.gauss(0.0, 0.04)))
        level["USDCNH"] *= math.exp(-0.1 * move + rng.gauss(0.0, 0.002))
        for series, value in level.items():
            if rng.random() < 0.02 or (series == "VIXCLS" and not vix):
                continue
            old = {"series_id": series, "observation_date": day, "units": MACRO_UNITS[series],
                   "source": "yahoo-chart" if series == "USDCNH" else "fred", "vintage": old_vintage,
                   "fetched_at": f"{old_vintage}T07:00:00Z"}
            if day > _shift_day(as_of, -14):
                macro += [{**old, "value": value * 1.01},
                          {**old, "value": value, "vintage": new_vintage, "fetched_at": f"{new_vintage}T07:00:00Z"}]
            else:
                macro.append({**old, "value": value})

    # 13F filings, in whole dollars so the database sums them exactly.
    ours = {cusip_from_isin(info["isin"]): conid for conid, info in instruments.items()
            if cusip_from_isin(info["isin"])}
    pool = [f"R{index:08d}" for index in range(200)]
    holdings = []

    def filing(cik, name, period, accession, filed_at, stakes):
        for cusip, value, *kind in stakes:
            put_call, shares_type = kind if kind else ("", "SH")
            holdings.append({"filer_cik": cik, "filer_name": name, "period": period, "cusip": cusip,
                             "issuer": f"Issuer {cusip}", "shares": float(round(value / 50.0)),
                             "shares_type": shares_type, "value_usd": float(value), "put_call": put_call,
                             "accession": accession, "filed_at": filed_at, "fetched_at": f"{filed_at}T08:00:00Z"})

    def mix(count, with_ours=True):
        mine = [(cusip, rng.randint(10_000, 2_000_000)) for cusip in sorted(ours) if with_ours and rng.random() < 0.7]
        return mine + [(cusip, rng.randint(10_000, 2_000_000)) for cusip in sorted(rng.sample(pool[:190], count))]

    (alpha, alpha_name), (beta, beta_name), (gamma, gamma_name), (gamma2, _), (delta, delta_name), \
        (echo, echo_name), (hotel, hotel_name) = FILERS
    some = sorted(ours)[0] if ours else pool[0]
    filing(alpha, alpha_name, "2026-06-30", "0000100001-26-000001", "2026-08-10", mix(30))
    filing(alpha, alpha_name, "2026-06-30", "0000100001-26-000002", "2026-08-20",
           mix(30) + [(some, 500_000, "PUT", "SH"), (pool[199], 300_000, "", "PRN")])
    filing(beta, beta_name, "2026-06-30", "0000100002-26-000001", "2026-08-14", mix(50) + [(pool[198], 0)])
    filing(beta, beta_name, "2026-09-30", "0000100002-26-000002", "2026-11-13", mix(50))
    filing(gamma, gamma_name, "2026-03-31", "0000100003-26-000001", "2026-05-15", mix(40))
    filing(gamma2, gamma_name, "2026-06-30", "0000100004-26-000001", "2026-08-12", mix(20))
    filing(delta, delta_name, "2026-06-30", "0000900001-26-000077", "2026-08-13", mix(60, with_ours=False))
    # Its amendment, filed later by another agent under a lower accession: this one counts.
    filing(delta, delta_name, "2026-06-30", "0000100005-26-000001", "2026-08-27", mix(60, with_ours=False))
    # Our long US names at our values (a 13F reports long positions only, so a short is not in it).
    filing(echo, echo_name, "2026-06-30", "0000100006-26-000001", "2026-08-11",
           [(cusip, round(float(held[conid]["market_value"]))) for cusip, conid in sorted(ours.items())
            if float(held[conid]["market_value"]) > 0])
    filing(hotel, hotel_name, "2026-09-30", "0000100007-26-000001", as_of, mix(25))
    return {"instruments": instruments, "prices": prices, "daily_volumes": volumes, "factor_returns": factors,
            "macro_series": macro, "institutional_holdings": holdings}


def market_from_raw(raw: dict, data: dict, *, as_of: str | None = None) -> dict:
    """What qs.load_phase3_inputs() reads from synthetic_market()'s rows once the four tables are
    in silver, worked out in plain Python: the reference the database readers are checked against."""
    from collections import defaultdict

    from ibkr_etl import market_inputs as mi

    as_of = as_of or data["as_of"]
    day = max(row["date"] for row in data["positions"] if row["date"] <= as_of)
    held = sorted({row["conid"] for row in data["positions"] if row["date"] == day})
    instruments = raw["instruments"]

    series = defaultdict(dict)
    for row in raw["daily_volumes"]:
        if _shift_day(day, -qs.VOLUME_WINDOW_DAYS) <= row["price_date"] <= day and row["volume"] > 0:
            series[row["instrument_id"]][row["price_date"]] = row["volume"]
    # By conid only: a holding's volumes are stored under it.
    volumes = {str(conid): {"instrument_id": str(conid), "volumes": dict(sorted(series[str(conid)].items()))}
               for conid in held if series.get(str(conid))}

    def latest(rows, key, start, field, wanted):
        chosen = {}
        for row in rows:
            if wanted(row) and start <= row[field] <= as_of:
                name = (row[key], row[field])
                if name not in chosen or (row["vintage"], row["fetched_at"]) > (chosen[name]["vintage"],
                                                                                chosen[name]["fetched_at"]):
                    chosen[name] = row
        return [chosen[name] for name in sorted(chosen)]

    start = min(f"{as_of[:4]}-01-01", _shift_day(as_of, -365))
    factors, sources, vintages = defaultdict(dict), defaultdict(int), []
    for row in latest(raw["factor_returns"], "factor", start, "return_date",
                      lambda r: r["region"] == qs.FACTOR_REGION and r["source"] == mi.FACTOR_SOURCE):
        factors[row["return_date"]][row["factor"]] = row["value"]
        sources[row["source"]] += 1
        vintages.append(row["vintage"])
    factor_returns = {"region": qs.FACTOR_REGION,
                      "factors": {d: dict(sorted(v.items())) for d, v in sorted(factors.items())},
                      "sources": dict(sorted(sources.items())),
                      "vintages": [min(vintages), max(vintages)] if vintages else None}

    levels, units, origins = defaultdict(dict), {}, {}
    for row in latest(raw["macro_series"], "series_id", _shift_day(as_of, -365 * qs.MACRO_YEARS - 7),
                      "observation_date", lambda r: r["series_id"] in mi.MACRO_SERIES):
        levels[row["series_id"]][row["observation_date"]] = row["value"]
        units[row["series_id"]], origins[row["series_id"]] = row["units"], row["source"]
    macro = {"series": {name: dict(sorted(values.items())) for name, values in sorted(levels.items())},
             "units": dict(sorted(units.items())), "sources": dict(sorted(origins.items()))}

    from ibkr_etl.sec_13f import cusip_from_isin

    cusips = {cusip_from_isin(instruments[conid]["isin"]) for conid in held} - {None}
    known = [row for row in raw["institutional_holdings"] if row["period"] <= as_of and row["filed_at"] <= as_of]
    newest = {}
    for row in known:
        rank = (row["period"], row["filed_at"], row["accession"])
        if row["filer_cik"] not in newest or rank > newest[row["filer_cik"]][0]:
            newest[row["filer_cik"]] = (rank, row)
    chosen = [row for row in known if newest[row["filer_cik"]][0][::2] == (row["period"], row["accession"])
              and row["put_call"] == "" and row["shares_type"] == "SH" and row["value_usd"] > 0]
    filers = []
    for cik in sorted(newest):
        rows = [row for row in chosen if row["filer_cik"] == cik]
        rest = [row["value_usd"] for row in rows if row["cusip"] not in cusips]
        (period, filed_at, accession), first = newest[cik]
        filers.append({"filer_cik": cik, "filer_name": first["filer_name"], "period": period, "accession": accession,
                       "filed_at": filed_at, "positions": len(rows),
                       "total_value": float(sum(row["value_usd"] for row in rows)),
                       "rest_value": float(sum(rest)), "rest_squares": float(sum(v * v for v in rest)),
                       "rest_positions": len(rest)})
    stakes = [{"filer_cik": row["filer_cik"], "cusip": row["cusip"], "shares": row["shares"],
               "value_usd": row["value_usd"]}
              for row in sorted(chosen, key=lambda r: (r["filer_cik"], r["cusip"])) if row["cusip"] in cusips]
    return {
        "liquidity": {"volumes": volumes},
        "factor_attribution": factor_returns,
        "macro": macro,
        "crowding": {"instruments": {conid: {"isin": instruments[conid]["isin"],
                                             "asset_category": instruments[conid]["asset_category"]}
                                     for conid in held},
                     "filings": {"filers": filers, "stakes": stakes}},
    }


def _finite(value) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(v) for v in value)
    return True


class SyntheticBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = synthetic_data(days=560)
        cls.inputs = qs.prepare_inputs(cls.data)
        cls.payloads = {name: qs.BUILDERS[name](cls.inputs[name]) for name in qs.SECTION_NAMES}

    def test_every_section_has_the_payload_keys_and_is_available(self):
        for name, payload in self.payloads.items():
            with self.subTest(section=name):
                self.assertEqual(set(payload), {"section", "model_id", "model_version", "as_of", "available",
                                                "reason", "inputs", "results"})
                self.assertEqual(payload["section"], name)
                self.assertEqual(payload["model_id"], qs.SECTION_BY_NAME[name].model_id)
                self.assertEqual(payload["as_of"], AS_OF)
                self.assertTrue(payload["available"], payload["reason"])
                self.assertIsNone(payload["reason"])

    def test_results_are_the_engine_outputs_under_the_call_names(self):
        expected = {
            "skill": {"skill_summary"},
            "decisions": {"trade_value_added", "skill_profile", "timing_cost", "counterfactual_portfolios"},
            "currency": {"mtd", "ytd"},
            "tail": {"evt_var_es", "tail_backtest_report", "forecasts"},
            "regime": {"regime_model", "regime_risk"},
            "holdings_risk": {"ledoit_wolf_covariance", "ex_ante_risk", "statistical_factors"},
            "correlation": {"dcc_correlation"},
            "concentration": {"concentration", "concentration_by_sector", "correlation_clusters"},
            "execution": {"implementation_shortfall", "by_month", "skipped"},
        }
        for name, keys in expected.items():
            with self.subTest(section=name):
                results = self.payloads[name]["results"]
                self.assertEqual(set(results), keys)
                for key, value in results.items():
                    if isinstance(value, dict) and "available" in value:
                        self.assertTrue(value["available"], f"{key}: {value.get('reason')}")
        self.assertEqual(self.payloads["skill"]["results"]["skill_summary"]["model_version"],
                         "quant-skill-statistics-v1")

    def test_payloads_are_json_with_finite_numbers(self):
        for name, payload in self.payloads.items():
            with self.subTest(section=name):
                json.dumps(payload, allow_nan=False)
                self.assertTrue(_finite(payload))
                self.assertEqual(qs.quality_status(payload), "pass")

    def test_ex_ante_contributions_add_up_to_the_volatility(self):
        risk = self.payloads["holdings_risk"]["results"]["ex_ante_risk"]
        total = math.fsum(row["contribution"] for row in risk["holdings"])
        self.assertAlmostEqual(total, risk["volatility"], places=12)
        self.assertGreater(risk["volatility"], 0)
        self.assertEqual(len(self.payloads["holdings_risk"]["results"]["statistical_factors"]["factors"]), 3)

    def test_weights_are_of_nav_and_the_five_largest_are_correlated(self):
        summary = self.payloads["correlation"]["inputs"]
        names = summary["names"]
        self.assertEqual(len(summary["correlated"]), 5)
        weights = sorted((abs(item["weight"]) for item in names.values()), reverse=True)
        self.assertEqual(sorted((abs(names[n]["weight"]) for n in summary["correlated"]), reverse=True), weights[:5])
        self.assertLess(summary["invested_weight"], 1.0)  # the cash is not a holding
        dcc = self.payloads["correlation"]["results"]["dcc_correlation"]
        self.assertTrue(dcc["available"])

    def test_currency_effects_add_up_to_the_excess_return(self):
        for period in ("mtd", "ytd"):
            with self.subTest(period=period):
                result = self.payloads["currency"]["results"][period]
                self.assertAlmostEqual(result["totals"]["total"], result["excess_return"], places=12)
                self.assertIn("HKD", {row["currency"] for row in result["currencies"]})
        ytd = self.inputs["currency"]["periods"]["ytd"]
        self.assertEqual(ytd["start_date"], "2025-12-31")  # the last NAV date before 1 Jan

    def test_tail_forecasts_are_today_s_one_day_figures(self):
        tail = self.payloads["tail"]["results"]
        forecast = tail["forecasts"]
        self.assertEqual((forecast["as_of"], forecast["horizon_days"], forecast["basis"]), (AS_OF, 1, "conditional"))
        self.assertEqual(set(forecast["levels"]), {"99", "99.5"})
        evt = tail["evt_var_es"]["conditional"]["levels"]["99"]
        self.assertEqual(forecast["levels"]["99"]["var"], evt["var"])
        self.assertGreater(forecast["levels"]["99.5"]["var"], forecast["levels"]["99"]["var"])

    def test_decisions_cover_the_trade_year_and_the_counterfactual_starts_before_1_january(self):
        decisions = self.payloads["decisions"]
        added = decisions["results"]["trade_value_added"]
        window_start = (date.fromisoformat(AS_OF) - timedelta(days=qs.TRADE_WINDOW_DAYS)).isoformat()
        self.assertTrue(all(row["trade_date"] > window_start for row in added["trades"]))
        self.assertEqual(added["horizons"], [5, 21, 63])
        self.assertEqual(decisions["results"]["skill_profile"]["horizon"], 21)
        counterfactual = decisions["results"]["counterfactual_portfolios"]
        self.assertEqual(counterfactual["start_date"], "2025-12-31")
        self.assertAlmostEqual(counterfactual["total_flows"], 0.0)  # the deposit was before the year
        self.assertIn("benchmark", counterfactual["summary"])

    def test_execution_is_summarised_by_month(self):
        execution = self.payloads["execution"]["results"]
        orders = execution["implementation_shortfall"]
        self.assertEqual(sum(month["orders"] for month in execution["by_month"]), len(orders))
        total = math.fsum(o["implementation_shortfall"]["total"]["amount"] for o in orders)
        self.assertAlmostEqual(total, math.fsum(month["total"] for month in execution["by_month"]), places=6)


class KnownAnswerTests(unittest.TestCase):
    def _holdings(self, weights: dict, sectors: dict, days: int = 300) -> dict:
        rng = random.Random(3)
        calendar = business_days(AS_OF, days)
        prices = {}
        for name in weights:
            level, series = 100.0, {}
            for day in calendar:
                level *= 1.0 + rng.gauss(0.0, 0.01)
                series[day] = level
            prices[name] = series
        return {"as_of": AS_OF, "holdings_date": AS_OF, "nav": 1.0, "prices": prices,
                "holdings": [{"name": name, "conid": str(i + 1), "currency": "USD", "sector": sectors[name],
                              "weight": weight, "market_value": weight} for i, (name, weight) in
                             enumerate(weights.items())]}

    def test_equal_weights_give_one_over_n(self):
        inputs = self._holdings({"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25},
                                {"A": "Tech", "B": "Tech", "C": "Energy", "D": "Energy"})
        results = qs.build_concentration(inputs)["results"]
        self.assertAlmostEqual(results["concentration"]["herfindahl"], 0.25)
        self.assertAlmostEqual(results["concentration"]["effective_holdings"], 4.0)
        self.assertAlmostEqual(results["concentration_by_sector"]["herfindahl"], 0.5)
        self.assertEqual(results["concentration_by_sector"]["holdings"], 2)
        # Independent random walks: no pair is correlated above 0.7, so every holding is its own cluster.
        self.assertTrue(results["correlation_clusters"]["available"])

    def test_one_order_against_its_previous_close(self):
        inputs = {"as_of": AS_OF, "currency": "USD", "no_trades": None,
                  "closes": {"1": {"2026-10-14": 10.0, "2026-10-15": 10.2}},
                  "trades": [{"trade_id": "T1", "trade_date": "2026-10-15", "conid": "1", "symbol": "A",
                              "side": "BUY", "quantity": 60.0, "price": 10.1, "commission": 0.5},
                             {"trade_id": "T2", "trade_date": "2026-10-15", "conid": "1", "symbol": "A",
                              "side": "BUY", "quantity": 40.0, "price": 10.1, "commission": 0.5}]}
        payload = qs.build_execution(inputs)
        order = payload["results"]["implementation_shortfall"][0]
        result = order["implementation_shortfall"]
        self.assertEqual((order["fills"], order["decision_date"]), (2, "2026-10-14"))
        self.assertAlmostEqual(result["decision_price"], 10.0)
        self.assertAlmostEqual(result["final_price"], 10.2)
        # 100 shares filled 0.10 above the previous close, plus 1.00 of commission.
        self.assertAlmostEqual(result["components"]["trading"]["amount"], 10.0)
        self.assertAlmostEqual(result["total"]["amount"], 11.0)
        self.assertAlmostEqual(payload["results"]["by_month"][0]["total_bps"], 11.0 / 1000.0 * 10_000)

    def test_a_buy_that_rises_adds_its_move_less_commission(self):
        calendar = business_days(AS_OF, 80)
        closes = {"1": {day: 10.0 + 0.1 * i for i, day in enumerate(calendar)}}
        trade_day = calendar[10]
        inputs = {"as_of": AS_OF, "currency": "USD", "no_trades": None, "closes": closes,
                  "sizes": {day: {"1": 1000.0} for day in calendar},
                  "trades": [{"trade_id": "T1", "trade_date": trade_day, "conid": "1", "symbol": "A", "side": "BUY",
                              "quantity": 100.0, "price": 11.0, "commission": 2.0}],
                  "counterfactual": {"unavailable": "not needed here"}}
        results = qs.build_decisions(inputs)["results"]
        five = results["trade_value_added"]["trades"][0]["horizons"][0]
        self.assertAlmostEqual(five["end_price"], 10.0 + 0.1 * 15)
        self.assertAlmostEqual(five["value_added"], (11.5 - 11.0) * 100 - 2.0)
        self.assertAlmostEqual(results["timing_cost"]["trades"][0]["decision_price"], 10.0 + 0.1 * 9)
        self.assertEqual(results["counterfactual_portfolios"], {"available": False, "reason": "not needed here"})


class UnavailableTests(unittest.TestCase):
    def test_own_pipelines_are_unavailable_in_every_section(self):
        data = {"subject_id": "alpha-world-ms", "as_of": AS_OF, "unavailable": "Alpha World MS and T report "
                "through their own HKD pipeline."}
        inputs = qs.prepare_inputs(data)
        for name in qs.SECTION_NAMES:
            with self.subTest(section=name):
                payload = qs.BUILDERS[name](inputs[name])
                self.assertFalse(payload["available"])
                self.assertIn("own HKD pipeline", payload["reason"])
                self.assertEqual(payload["results"], {})
                self.assertEqual(qs.quality_status(payload), "warn")

    def test_without_a_comparison_the_return_sections_say_why(self):
        data = synthetic_data(days=120)
        data["comparison"] = []
        inputs = qs.prepare_inputs(data)
        for name in ("skill", "tail", "regime"):
            payload = qs.BUILDERS[name](inputs[name])
            self.assertFalse(payload["available"])
            self.assertIn("No primary benchmark comparison", payload["reason"])

    def test_a_short_history_keeps_what_it_can(self):
        tail = qs.build_tail(qs.prepare_inputs(synthetic_data(days=320))["tail"])
        self.assertTrue(tail["available"])  # the tail fit works on 319 returns
        self.assertTrue(tail["results"]["evt_var_es"]["available"])
        self.assertFalse(tail["results"]["tail_backtest_report"]["available"])
        self.assertIn("500", tail["results"]["tail_backtest_report"]["reason"])
        self.assertEqual(qs.quality_status(tail), "warn")
        inputs = qs.prepare_inputs(synthetic_data(days=120))
        short = qs.build_tail(inputs["tail"])
        self.assertFalse(short["available"])
        self.assertIn("at least 30 losses", short["reason"])
        regime = qs.build_regime(inputs["regime"])
        self.assertFalse(regime["available"])
        self.assertIn("126", regime["reason"])
        correlation = qs.build_correlation(inputs["correlation"])
        self.assertFalse(correlation["available"])  # DCC needs 250 days

    def test_a_derived_subject_has_no_trades(self):
        data = synthetic_data(days=300, subject_type="filtered_portfolio")
        inputs = qs.prepare_inputs(data)
        decisions = qs.build_decisions(inputs["decisions"])
        self.assertTrue(decisions["available"])  # the counterfactual still works
        self.assertIn("physical accounts only", decisions["results"]["trade_value_added"]["reason"])
        execution = qs.build_execution(inputs["execution"])
        self.assertFalse(execution["available"])
        self.assertIn("physical accounts only", execution["reason"])

    def test_no_holdings(self):
        data = synthetic_data(days=60)
        data["positions"] = []
        inputs = qs.prepare_inputs(data)
        for name in ("holdings_risk", "correlation", "concentration"):
            payload = qs.BUILDERS[name](inputs[name])
            self.assertFalse(payload["available"])
            self.assertIn("no holdings", payload["reason"])

    def test_non_finite_numbers_are_stored_as_null_and_warn(self):
        payload = {"available": True, "results": {"x": {"value": float("nan"), "list": [1.0, float("inf")]}}}
        cleaned, replaced = qs.finite_copy(payload)
        self.assertEqual(replaced, 2)
        self.assertEqual(cleaned["results"]["x"], {"value": None, "list": [1.0, None]})
        self.assertEqual(qs.quality_status(cleaned, replaced), "warn")


class FingerprintTests(unittest.TestCase):
    def test_same_inputs_same_fingerprint_and_any_change_shows(self):
        first = qs.prepare_inputs(synthetic_data(days=90))
        again = qs.prepare_inputs(synthetic_data(days=90))
        for name in qs.SECTION_NAMES:
            with self.subTest(section=name):
                print_one = qs.section_fingerprint(name, first[name])
                self.assertRegex(print_one, "^[0-9a-f]{64}$")
                self.assertEqual(print_one, qs.section_fingerprint(name, again[name]))
                reordered = json.loads(json.dumps(first[name]), object_pairs_hook=lambda pairs: dict(reversed(pairs)))
                self.assertEqual(print_one, qs.section_fingerprint(name, reordered))
        changed = synthetic_data(days=90)
        changed["comparison"][-1]["portfolio_return"] += 0.001
        moved = qs.prepare_inputs(changed)
        self.assertNotEqual(qs.section_fingerprint("skill", first["skill"]),
                            qs.section_fingerprint("skill", moved["skill"]))
        self.assertEqual(qs.section_fingerprint("concentration", first["concentration"]),
                         qs.section_fingerprint("concentration", moved["concentration"]))
        self.assertNotEqual(qs.section_fingerprint("skill", first["skill"]),
                            qs.section_fingerprint("tail", first["skill"]))

    def test_the_versions_are_part_of_it(self):
        inputs = qs.prepare_inputs(synthetic_data(days=60))["tail"]
        before = qs.section_fingerprint("tail", inputs)
        with patch.object(qs, "QUANT_ANALYTICS_VERSION", "quant-analytics-v2"):
            self.assertNotEqual(before, qs.section_fingerprint("tail", inputs))
        with patch.object(qs.model_cards, "version", return_value="tail-risk-v9"):
            self.assertNotEqual(before, qs.section_fingerprint("tail", inputs))

    def test_non_finite_inputs_are_refused(self):
        with self.assertRaises(ValueError):
            qs.section_fingerprint("tail", {"as_of": AS_OF, "returns": [[AS_OF, float("nan")]]})


class SettingTests(unittest.TestCase):
    def test_only_on_switches_the_build_on(self):
        for value, expected in (("on", True), (" ON ", True), ("off", False), ("", False), ("true", False),
                                ("of", False)):
            with self.subTest(value=value):
                self.assertEqual(qs.build_enabled({qs.BUILD_SETTING: value}), expected)
        self.assertFalse(qs.build_enabled({}))

    def test_the_job_does_nothing_when_off(self):
        spec = importlib.util.spec_from_file_location("build_quant_analytics",
                                                      ROOT / "scripts" / "build_quant_analytics.py")
        job = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(job)
        with patch.dict("os.environ", {qs.BUILD_SETTING: "off"}), \
                patch.object(job, "connect", side_effect=AssertionError("must not connect")), \
                patch("builtins.print") as printed:
            self.assertEqual(job.main([]), 0)
        self.assertIn('"status": "off"', printed.call_args.args[0])

    def test_sections_are_named(self):
        self.assertEqual(len(qs.SECTIONS), 9)
        self.assertEqual(qs.section_names(["tail", "skill", "tail"]), ("skill", "tail"))
        with self.assertRaises(ValueError):
            qs.section_names(["liquidity"])


class PerformanceTests(unittest.TestCase):
    def test_sixty_holdings_and_three_years_build_well_under_a_minute(self):
        data = synthetic_data(holdings=60, days=756, trade_every=2)
        started = time.perf_counter()
        inputs = qs.prepare_inputs(data)
        timings = {"prepare": time.perf_counter() - started}
        for name in qs.SECTION_NAMES:
            section_started = time.perf_counter()
            payload = qs.BUILDERS[name](inputs[name])
            timings[name] = time.perf_counter() - section_started
            self.assertTrue(payload["available"], f"{name}: {payload['reason']}")
        # The phase 3 sections on their silver rows (QUANT_ANALYTICS_PHASE3), each timed too.
        fixture_started = time.perf_counter()
        market = market_from_raw(synthetic_market(data), data)  # the silver rows as read: not part of the build
        fixture = time.perf_counter() - fixture_started
        market_started = time.perf_counter()
        phase3 = qs.prepare_phase3_inputs(data, inputs, market)
        timings["prepare phase 3"] = time.perf_counter() - market_started
        for name in qs.PHASE3_NAMES:
            section_started = time.perf_counter()
            payload = qs.BUILDERS[name](phase3[name])
            timings[name] = time.perf_counter() - section_started
            self.assertTrue(payload["available"], f"{name}: {payload['reason']}")
        total = time.perf_counter() - started - fixture
        print("\nquant build, 60 holdings and 3 years: " + ", ".join(f"{k} {v:.2f}s" for k, v in timings.items())
              + f"; total {total:.2f}s")
        self.assertLess(total, 60.0)


if __name__ == "__main__":
    unittest.main()
