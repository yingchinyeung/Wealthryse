from __future__ import annotations

import unittest
from pathlib import Path

from ibkr_etl.agent_tools import (
    AgentToolContext,
    AgentToolError,
    execute_tool,
    tool_catalog,
)


class _Cursor:
    def __init__(self, row=None, rows=None) -> None:
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, latest: str = "2026-09-18") -> None:
        self.latest = latest
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql: str, parameters=()) -> _Cursor:
        self.calls.append((sql, tuple(parameters)))
        if "MAX(snapshot_date) AS latest" in sql and "subject_nav_series" in sql:
            return _Cursor({"latest": self.latest})
        if "SELECT subject_type FROM platform_subjects" in sql:
            return _Cursor({"subject_type": "physical_account"})
        if "MAX(snapshot_date) AS latest" in sql and "subject_position_series" in sql:
            return _Cursor({"latest": self.latest})
        if "instrument_classifications" in sql:
            return _Cursor(rows=[])
        raise AssertionError(f"Unexpected SQL in policy test: {sql}")


class AgentToolPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = AgentToolContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            actor_object_id="approved-object-id",
            allowed_subject_ids=frozenset({"U16088571"}),
            correlation_id="correlation-123",
        )

    def test_catalog_contains_only_narrow_read_only_tools(self) -> None:
        catalog = tool_catalog()
        names = {item["name"] for item in catalog}
        self.assertEqual(
            names,
            {
                "get_portfolio_summary",
                "get_performance_series",
                "get_holdings",
                "compare_with_benchmarks",
                "get_risk_metrics",
                "get_data_freshness",
                "get_sector_allocation",
            },
        )
        serialized = str(catalog).lower()
        for prohibited in ("execute_sql", "shell", "trade", "secret", "permission"):
            self.assertNotIn(prohibited, serialized)
        for item in catalog:
            self.assertFalse(item["input_schema"]["additionalProperties"])

    def test_implementation_has_no_write_or_process_primitives(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "ibkr_etl"
            / "agent_tools.py"
        ).read_text(encoding="utf-8").lower()
        for sql_verb in ("insert into", "update ", "delete from", "truncate ", "alter table", "drop table"):
            self.assertNotIn(sql_verb, source)
        for process_primitive in ("subprocess", "os.system", "shell=true"):
            self.assertNotIn(process_primitive, source)

    def test_performance_range_compares_unified_text_dates_portably(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "ibkr_etl"
            / "agent_tools.py"
        ).read_text(encoding="utf-8")
        self.assertIn("SELECT history_start_date::text FROM platform_subjects", source)
        self.assertNotIn(
            "SELECT history_start_date FROM platform_subjects WHERE subject_id = %s",
            source,
        )

    def test_unknown_tool_is_denied_before_database_access(self) -> None:
        connection = _Connection()
        with self.assertLogs("uvicorn.error.ibkr_agent", level="INFO") as logs:
            with self.assertRaises(AgentToolError) as raised:
                execute_tool(
                    connection,
                    self.context,
                    "execute_sql",
                    {"subject_id": "U16088571", "sql": "SELECT 1"},
                )
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.code, "tool_not_allowed")
        self.assertEqual(connection.calls, [])
        self.assertIn('"status":"denied"', "".join(logs.output))

    def test_unapproved_account_is_denied_before_database_access(self) -> None:
        connection = _Connection()
        with self.assertRaises(AgentToolError) as raised:
            execute_tool(
                connection,
                self.context,
                "get_portfolio_summary",
                {"subject_id": "U-NOT-AUTHORIZED"},
            )
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.code, "subject_not_authorized")
        self.assertEqual(connection.calls, [])

    def test_unexpected_arguments_are_denied(self) -> None:
        connection = _Connection()
        with self.assertRaises(AgentToolError) as raised:
            execute_tool(
                connection,
                self.context,
                "get_holdings",
                {"subject_id": "U16088571", "include_secrets": True},
            )
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.code, "unexpected_arguments")
        self.assertEqual(connection.calls, [])

    def test_sector_tool_fails_closed_without_validated_classification(self) -> None:
        connection = _Connection()
        with self.assertLogs("uvicorn.error.ibkr_agent", level="INFO") as logs:
            result = execute_tool(
                connection,
                self.context,
                "get_sector_allocation",
                {"subject_id": "U16088571", "as_of": "2026-09-18"},
            )
        self.assertEqual(result["quality_status"], "fail")
        self.assertFalse(result["data"]["available"])
        self.assertIn("No classified position rows", result["data"]["reason"])
        joined = "".join(logs.output)
        self.assertIn('"status":"success"', joined)
        self.assertNotIn("approved-object-id", joined)

    def test_invalid_date_is_rejected_without_querying_portfolio_data(self) -> None:
        connection = _Connection()
        with self.assertRaises(AgentToolError) as raised:
            execute_tool(
                connection,
                self.context,
                "get_portfolio_summary",
                {"subject_id": "U16088571", "as_of": "18/09/2026"},
            )
        self.assertEqual(raised.exception.code, "invalid_arguments")
        self.assertEqual(connection.calls, [])


if __name__ == "__main__":
    unittest.main()
