"""WealthRyse agents: a read-only tool server, an agent runtime and scheduled jobs.

Separate from ibkr_etl so the website release path (which ships src/ibkr_etl
only) is unaffected. It reuses ibkr_etl's pure calculation code so every
figure an agent quotes matches the website and the factsheets.
"""
