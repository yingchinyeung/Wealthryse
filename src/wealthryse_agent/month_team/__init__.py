"""The month-end agent team (docs/AGENTS.md, "Month-end team").

Six agents take each registered account from month close to a checked
month-end package:

- coordinator (``coordinator.py``): one stage machine per account and month;
- data (``agents.DataAgent``): reconciles the month, verdict clean, fixable or blocked;
- analyst (``agents.Analyst``): the cited figure set, from the read-only tools;
- writer (``agents.Writer``): commentary from the figure set only, no digits;
- reviewer (``agents.Reviewer``): checks the commentary and every printed figure, cold;
- visual QA (``agents.VisualQA``): looks at each rendered page with the vision model.

The team reads through the tool server and the review files, and writes
only its own run tables and inbox cards. It has no route to a release, a
rebuild or any platform table. Every model call uses the APAC Data Zone
deployment. Until AGENT_MONTH_TEAM_LIVE is on (together with the factsheet
job's team-commentary setting) it runs in shadow: its commentary is shown,
never used.
"""
