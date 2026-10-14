"""Which accounts a caller may see. Resolved in the database on every call."""
from __future__ import annotations

import re

from ibkr_etl.db import PostgresConnection

OBJECT_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
JOBS = frozenset({"holdings_explainer", "month_end_drafter", "data_quality", "month_team", "onboarding",
                  "operations", "weekly_brief", "live_market"})


def user_accounts(connection: PostgresConnection, user_object_id: str) -> list[str]:
    if not OBJECT_ID.match(user_object_id or ""):
        return []
    try:
        rows = connection.execute("SELECT agent.allowed_subjects(?) AS subject_id", (user_object_id.lower(),)).fetchall()
        return [str(r["subject_id"]) for r in rows]
    finally:
        connection.rollback()


def job_accounts(connection: PostgresConnection, job_name: str) -> list[str]:
    if job_name not in JOBS:
        return []
    try:
        rows = connection.execute("SELECT agent.service_subjects(?) AS subject_id", (job_name,)).fetchall()
        return [str(r["subject_id"]) for r in rows]
    finally:
        connection.rollback()
