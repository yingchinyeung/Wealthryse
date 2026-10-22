"""Who may write the silver layer: the register behind the silver write lock.

Plain data, no behaviour. SILVER_RELATIONS is the `moved` list of migration 070 (the tables
and views now in the `silver` schema, still reachable as public.<name>). LOADER_JOBS are the
Container Apps jobs (their Bicep names) whose runs may write silver. WRITER_FILES lists every
Python file under src/ and scripts/ that holds SQL writing a silver relation (INSERT, UPDATE,
DELETE, TRUNCATE, COPY, MERGE, LOCK TABLE, SELECT ... FOR UPDATE / SHARE), with the relations
it writes and the jobs that run it, or a NOT_A_JOB marker when no loader job runs it against
the live tables. The silver.<table>_history tables are written by a trigger, never by code,
so they are not listed.

tests/test_silver_writers.py scans the code and fails when a file writes silver without an
entry here, when an entry no longer writes, or when SILVER_RELATIONS drifts from 070. A new
writer goes in WRITER_FILES with its job; a new job that writes goes in LOADER_JOBS as well.
Every other job (risk-snapshots, crisis-scenarios, certified-figures, month-end-factsheet,
autonomous-admin, the agents) and the web app only read silver.
"""
from __future__ import annotations

SILVER_RELATIONS: tuple[str, ...] = (
    "accounts",
    "benchmark_prices",
    "cash_snapshots",
    "cash_transactions",
    "corporate_actions",
    "derived_portfolio_cash_balances",
    "derived_portfolio_cash_ledger",
    "derived_portfolio_exclusions",
    "derived_portfolio_nav",
    "derived_portfolio_positions",
    "fx_rates",
    "fx_transactions",
    "instrument_classifications",
    "instruments",
    "market_data_prices",
    "nav_snapshots",
    "position_snapshots",
    "reference_fx_rates",
    "risk_free_rates",
    "synthetic_portfolio_components",
    "trades",
    "subject_cash_activity_series",
    "subject_cash_balance_series",
    "subject_live_positions",
    "subject_live_summary",
    "subject_nav_series",
    "subject_position_series",
    "v_daily_nav",
    "v_latest_positions",
)

# A job name that carries an account number is written with the number cut to "u…".
LOADER_JOBS: dict[str, str] = {
    "ibkrv2stg-institutional-daily":
        "the daily load: Flex statements through the loader, and the Kiwoom collector with its English names",
    "ibkrv2stg-flex-discovery":
        "checks a newly found Flex query by loading its statement through the loader",
    "ibkrv2stg-import-u…":
        "the import job: loads one account's snapshot into the IBKR tables",
    "ibkrv2stg-benchmark-daily":
        "benchmark prices, reference FX rates and the crisis-replay market data prices",
    "ibkrv2stg-risk-free-daily":
        "risk-free rates (HIBOR, Korea CD and the other series)",
    "ibkrv2stg-sector-classification":
        "instrument classifications, and English instrument names (instruments, which lineage leaves out)",
    "ibkrv2stg-derived-daily":
        "derived portfolio tables and components, market data, benchmark, classifications and "
        "English instrument names (instruments, which lineage leaves out)",
    "ibkrv2stg-onboard-analytics":
        "started by the web app for a new account: benchmark, crisis-replay prices, classifications, English names",
    "ibkrv2stg-onboard-render":
        "started by the web app to render a new account's reports; also runs one-off scripts as an override",
    "ibkrv2stg-bootstrap":
        "the admin login: migrations, the reference subject rename and the silver history baseline (as an override)",
}

LOCAL_ONLY = "local/sqlite only"
TEMP_ONLY = "temp tables or a scratch schema only"
ONE_OFF_RENDER = "one-off script run as an onboard-render override"
ONE_OFF = "one-off script run by hand or as a job command override"
NOT_A_JOB: tuple[str, ...] = (LOCAL_ONLY, TEMP_ONLY, ONE_OFF_RENDER, ONE_OFF)

_ONBOARDING = ("ibkrv2stg-onboard-analytics", "ibkrv2stg-onboard-render")

# path -> (silver relations it writes, sorted; the jobs that run it, or a NOT_A_JOB marker)
WRITER_FILES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # --- loader jobs
    "src/ibkr_etl/loader.py": (
        ("accounts", "cash_snapshots", "cash_transactions", "corporate_actions", "fx_rates",
         "fx_transactions", "instruments", "nav_snapshots", "position_snapshots", "trades"),
        ("ibkrv2stg-institutional-daily", "ibkrv2stg-flex-discovery"),
    ),
    "src/ibkr_etl/kiwoom_collector.py": (
        ("accounts", "cash_snapshots", "cash_transactions", "fx_rates", "instruments",
         "nav_snapshots", "position_snapshots", "trades"),
        ("ibkrv2stg-institutional-daily",),
    ),
    "src/ibkr_etl/kiwoom_us_account.py": (
        ("accounts",),
        ("ibkrv2stg-institutional-daily",),
    ),
    # apply_english_names runs in the collector and in every classification refresh;
    # translate_account runs from scripts/translate_kiwoom_text.py.
    "src/ibkr_etl/kiwoom_english.py": (
        ("cash_transactions", "instruments", "trades"),
        ("ibkrv2stg-institutional-daily", "ibkrv2stg-sector-classification", "ibkrv2stg-derived-daily",
         *_ONBOARDING, ONE_OFF),
    ),
    "src/ibkr_etl/pilot_import.py": (
        ("accounts", "cash_snapshots", "cash_transactions", "corporate_actions", "fx_rates",
         "fx_transactions", "instruments", "nav_snapshots", "position_snapshots", "trades"),
        ("ibkrv2stg-import-u…",),
    ),
    "src/ibkr_etl/provisional_benchmark.py": (
        ("benchmark_prices", "reference_fx_rates"),
        ("ibkrv2stg-benchmark-daily", "ibkrv2stg-derived-daily", "ibkrv2stg-onboard-analytics"),
    ),
    "src/ibkr_etl/crisis_replay.py": (
        ("market_data_prices",),
        ("ibkrv2stg-benchmark-daily", "ibkrv2stg-derived-daily", *_ONBOARDING),
    ),
    "src/ibkr_etl/hibor.py": (("risk_free_rates",), ("ibkrv2stg-risk-free-daily",)),
    "src/ibkr_etl/korea_cd_rate.py": (("risk_free_rates",), ("ibkrv2stg-risk-free-daily",)),
    "src/ibkr_etl/risk_free.py": (("risk_free_rates",), ("ibkrv2stg-risk-free-daily",)),
    "src/ibkr_etl/sector_classification.py": (
        ("instrument_classifications",),
        ("ibkrv2stg-sector-classification", "ibkrv2stg-derived-daily", *_ONBOARDING),
    ),
    "src/ibkr_etl/derived_portfolio.py": (
        ("derived_portfolio_cash_balances", "derived_portfolio_cash_ledger", "derived_portfolio_exclusions",
         "derived_portfolio_nav", "derived_portfolio_positions", "synthetic_portfolio_components"),
        ("ibkrv2stg-derived-daily",),
    ),
    "src/ibkr_etl/market_data.py": (("market_data_prices",), ("ibkrv2stg-derived-daily",)),
    "src/ibkr_etl/reference_subject_rename.py": (
        ("synthetic_portfolio_components",),
        ("ibkrv2stg-bootstrap",),
    ),
    # scripts/medallion_silver_history.py (backfill, fx-merge), run on bootstrap with the admin login;
    # it also replays raw files into SQLite, which is not silver.
    "src/ibkr_etl/silver_history.py": (
        ("cash_snapshots", "cash_transactions", "corporate_actions", "fx_rates", "fx_transactions",
         "instruments", "nav_snapshots", "position_snapshots", "trades"),
        ("ibkrv2stg-bootstrap",),
    ),
    # --- one-off repairs that wrote the live tables
    "scripts/apply_alpha_ms_hkd_cutover.py": (
        ("derived_portfolio_cash_balances", "derived_portfolio_cash_ledger", "derived_portfolio_exclusions",
         "derived_portfolio_nav", "derived_portfolio_positions", "risk_free_rates",
         "synthetic_portfolio_components"),
        (ONE_OFF_RENDER,),
    ),
    "scripts/backfill_alpha_t_cash_20261009.py": (("cash_snapshots",), (ONE_OFF_RENDER,)),
    "scripts/import_alpha_ms_recovered_positions.py": (("position_snapshots",), (ONE_OFF,)),
    "src/ibkr_etl/position_recovery.py": (("position_snapshots",), (ONE_OFF,)),  # only that script uses it
    # --- temp tables or a scratch schema, never the live tables
    "scripts/probe_upgrade_institutional_postgres_20261010.py": (
        ("instruments", "subject_position_series"),
        (TEMP_ONLY,),
    ),
    "scripts/probe_upgrade_sync_postgres_20261010.py": (("accounts", "cash_snapshots"), (TEMP_ONLY,)),
    "scripts/qualify_alpha_ms_hkd_postgres.py": (("risk_free_rates",), (TEMP_ONLY,)),
    "scripts/verify_fx_evidence_schema.py": (("accounts",), (TEMP_ONLY,)),
    # --- SQLite files and in-memory fixtures
    "scripts/qualify_alpha_ms_hibor_feed.py": (("risk_free_rates",), (LOCAL_ONLY,)),
    "scripts/qualify_alpha_ms_step3_cache.py": (("risk_free_rates", "subject_nav_series"), (LOCAL_ONLY,)),
    "scripts/qualify_alpha_world_t_history.py": (
        ("accounts", "fx_rates", "market_data_prices", "nav_snapshots"),
        (LOCAL_ONLY,),
    ),
    "src/ibkr_etl/analyst_export.py": (
        ("accounts", "cash_snapshots", "cash_transactions", "corporate_actions",
         "derived_portfolio_cash_balances", "derived_portfolio_cash_ledger", "derived_portfolio_exclusions",
         "derived_portfolio_nav", "derived_portfolio_positions", "fx_rates", "fx_transactions",
         "instruments", "market_data_prices", "nav_snapshots", "position_snapshots",
         "synthetic_portfolio_components", "trades", "v_daily_nav", "v_latest_positions"),
        (LOCAL_ONLY,),
    ),
    "src/ibkr_etl/pilot_snapshot.py": (
        ("accounts", "cash_snapshots", "cash_transactions", "corporate_actions", "fx_rates",
         "fx_transactions", "instruments", "nav_snapshots", "position_snapshots", "trades"),
        (LOCAL_ONLY,),
    ),
}

# path -> (silver relations a write the scan cannot follow may reach, why it is safe). Empty:
# every table name the scan finds resolves.
UNRESOLVED_WRITES: dict[str, tuple[tuple[str, ...], str]] = {}
