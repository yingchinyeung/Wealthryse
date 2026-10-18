"""The research zone's code: point-in-time data, backtests, validation and the experiment log.

This is the only package allowed to name research objects (the ``research`` schema on the
research server and the ``research-*`` containers); tests/test_research_isolation.py fails if
anything else does. The website, the agents and the gold builds never import it. A research
result reaches people only as a gold certified figure, through the publish gate, with a model
card and the owner's approval. See docs/MEDALLION_ARCHITECTURE.md.

Modules:

* :mod:`.point_in_time` – what was known on a given date: versioned records, knowledge times
  and a view that refuses to look ahead.
* :mod:`.backtester` – an event-driven daily backtest with costs, using only point-in-time data.
* :mod:`.validation` – walk-forward splits and purged k-fold cross-validation with an embargo.
* :mod:`.experiment_log` – an append-only log of every trial, so overfitting can be measured.
"""
