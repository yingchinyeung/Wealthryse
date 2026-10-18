"""The release data report (scripts/release_data_report.py) on synthetic snapshot files.

No database: tables are encoded from synthetic rows, written as snapshot files and
compared. tests/test_release_data_report_db.py runs the same tool on PostgreSQL.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("release_data_report", ROOT / "scripts" / "release_data_report.py")
report_tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(report_tool)

HEADER = {"format": report_tool.FORMAT, "version": report_tool.FORMAT_VERSION, "taken_at": "2026-10-18 06:00:00 UTC",
          "server_version": "16.4", "all_tenants": False, "default_tenant": True,
          "migrations": ["069_medallion_bronze_layer.sql", "070_medallion_silver_layer.sql"]}
NAV_COLUMNS = [["account_id", "text"], ["snapshot_date", "text"], ["currency", "text"], ["total_text", "text"],
               ["row_sha256", "text"], ["last_seen_run_id", "bigint"]]
RETURN_COLUMNS = [["subject_id", "text"], ["observation_date", "text"], ["portfolio_return", "double precision"],
                  ["quality_status", "text"], ["calculation_version", "text"], ["generated_at", "text"]]


def nav(rows):
    return report_tool.encode_table("silver.nav_snapshots", "silver", NAV_COLUMNS,
                                    ["account_id", "snapshot_date", "currency"], rows)


def returns(rows, columns=RETURN_COLUMNS):
    return report_tool.encode_table("gold.performance_comparison_daily", "gold", columns,
                                    ["subject_id", "observation_date", "calculation_version"], rows)


def source(name, *tables, header=HEADER):
    return (name, header, iter(tables))


class EncodingTests(unittest.TestCase):
    def test_column_kinds(self):
        kind = report_tool.column_kind
        self.assertEqual(kind("subject_id", "text", ["subject_id"]), "key")
        self.assertEqual(kind("generated_at", "text", []), "ignored")
        self.assertEqual(kind("payload_json", "jsonb", []), "json")
        self.assertEqual(kind("quality_status", "text", []), "category")
        self.assertEqual(kind("available", "boolean", []), "category")
        self.assertEqual(kind("amount_text", "text", []), "number")
        self.assertEqual(kind("price", "numeric(18,6)", []), "number")
        self.assertEqual(kind("description", "text", []), "text")
        self.assertEqual(kind("source_json", "text", []), "text")  # raw attributes, hashed

    def test_rows_hold_digests_not_keys_names_or_text(self):
        entry = nav([("U9400001", "2026-10-15", "USD", "1000.50", "ab" * 32, 7)])
        stored = json.dumps(entry)
        self.assertNotIn("U9400001", stored)
        self.assertEqual(entry["values"], ["total_text", "row_sha256"])  # last_seen_run_id is not compared
        key, subject, day, values = entry["rows"][0]
        self.assertEqual((len(key), len(subject), day), (24, 16, "2026-10-15"))
        self.assertEqual(values[0], 1000.5)
        self.assertTrue(values[1].startswith("#"))
        self.assertEqual(entry["group_kinds"], {subject: "u"})

    def test_json_numbers_and_paths(self):
        value = report_tool._json_value({"metrics": {"vol": 0.1}, "holdings": {"ZZTEST": {"weight": 0.5}},
                                         "series": [{"value": 1}, {"value": 2}], "generated_at": "x", "ok": True})
        self.assertNotIn("ZZTEST", json.dumps(value))
        labels = sorted(report_tool.path_label(path) for path in value["n"])
        self.assertEqual(labels, ["holdings.*.weight", "metrics.vol", "series[].value", "series[].value"])
        later = report_tool._json_value({"metrics": {"vol": 0.1}, "holdings": {"ZZTEST": {"weight": 0.5}},
                                         "series": [{"value": 1}, {"value": 2}], "generated_at": "y", "ok": True})
        self.assertEqual(value, later)  # generated_at inside a payload is ignored too
        self.assertEqual(report_tool._json_value('{"a": 1}'), report_tool._json_value({"a": 1}))

    def test_numbers_from_text_and_dates(self):
        self.assertEqual(report_tool._number(" 1,234.5 "), 1234.5)
        self.assertTrue(report_tool._number("n/a").startswith("#"))
        self.assertEqual(report_tool._number(float("nan")), "nan")
        self.assertEqual(report_tool._day("20261015"), "2026-10-15")
        self.assertEqual(report_tool._day("2026-10-15T08:00:00"), "2026-10-15")
        self.assertIsNone(report_tool._day("Jane Example"))
        self.assertEqual(report_tool._category("held for U9400001"), "held for u…")


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.before_returns = [("U9400001", "2026-10-14", 0.10, "pass", "v1", "t1"),
                               ("U9400001", "2026-10-15", 0.20, "pass", "v1", "t1"),
                               ("U9400002", "2026-10-15", 0.30, "pass", "v1", "t1"),
                               ("composite-x", "2026-10-15", 0.40, "pass", "v1", "t1")]
        self.before_nav = [("U9400001", "2026-10-15", "USD", "1000.50", "a", 1)]

    def build(self, after_returns, after_nav=None, before_header=HEADER, after_header=HEADER):
        return report_tool.build_report(
            source("snapshot before.jsonl", returns(self.before_returns), nav(self.before_nav), header=before_header),
            source("snapshot after.jsonl", returns(after_returns), nav(after_nav or self.before_nav),
                   header=after_header))

    def test_rebuild_with_new_times_and_float_noise_is_unchanged(self):
        rebuilt = [(s, d, r * (1 + 1e-12), q, v, "t2") for s, d, r, q, v, _ in self.before_returns]
        report = self.build(rebuilt)
        self.assertFalse(report_tool.has_changes(report))
        self.assertEqual(report["summary"]["unchanged"], ["gold.performance_comparison_daily", "silver.nav_snapshots"])
        markdown = report_tool.render_markdown(report)
        self.assertIn("**No figures changed.** All 2 silver and gold tables are unchanged.", markdown)
        self.assertNotIn("<details>", markdown)

    def test_changed_added_removed_and_largest_changes(self):
        after = [("U9400001", "2026-10-14", 0.10, "pass", "v1", "t2"),
                 ("U9400001", "2026-10-15", 0.26, "warn", "v1", "t2"),
                 ("U9400002", "2026-10-15", 0.33, "pass", "v1", "t2"),
                 ("U9400002", "2026-10-16", 0.50, "pass", "v1", "t2")]
        report = self.build(after)
        self.assertTrue(report_tool.has_changes(report))
        table = report["tables"]["gold.performance_comparison_daily"]
        self.assertEqual(table["rows"], {"before": 4, "after": 4, "added": 1, "removed": 1, "changed": 2})
        stats = table["columns"]["portfolio_return"]
        self.assertEqual(stats["changed"], 2)
        self.assertAlmostEqual(stats["max_abs"]["delta"], 0.06)
        self.assertAlmostEqual(stats["max_rel"]["ratio"], 0.3)
        self.assertEqual(stats["max_abs"]["date"], "2026-10-15")
        labels = {stats["max_abs"]["subject"], stats["max_rel"]["subject"]}
        self.assertTrue(labels <= {"u…#1", "u…#2"})
        self.assertEqual(table["transitions"], {"quality_status": {"pass → warn": 1}})
        self.assertEqual(set(table["groups"]), {"u…#1", "u…#2", "s…#1"})
        self.assertEqual(table["groups"]["s…#1"]["removed"], 1)
        biggest = report["biggest_changes"][0]
        self.assertEqual(biggest["table"], "gold.performance_comparison_daily")
        self.assertEqual(biggest["largest"]["column"], "portfolio_return")
        markdown = report_tool.render_markdown(report)
        self.assertIn("**1 of 2 tables changed; 1 unchanged.**", markdown)
        self.assertIn("**Unchanged (1):** silver.nav_snapshots.", markdown)
        self.assertIn("`portfolio_return` 2 values; Δ up to +0.06", markdown)
        self.assertIn("relative up to +30%", markdown)
        text = markdown + json.dumps(report, ensure_ascii=False)
        for secret in ("U9400001", "U9400002", "composite-x", "0.26", "0.33"):
            self.assertNotIn(secret, text)

    def test_set_cleared_and_text_values(self):
        after_nav = [("U9400001", "2026-10-15", "USD", None, "b", 2)]
        report = self.build(self.before_returns, after_nav)
        stats = report["tables"]["silver.nav_snapshots"]["columns"]
        self.assertEqual((stats["total_text"]["cleared"], stats["total_text"]["max_abs"]), (1, None))
        self.assertEqual(stats["row_sha256"]["changed"], 1)
        self.assertNotIn("last_seen_run_id", stats)

    def test_new_gone_and_added_columns(self):
        report = report_tool.build_report(source("a", nav(self.before_nav)), source("b", returns(self.before_returns)))
        self.assertEqual(report["summary"]["new"], ["gold.performance_comparison_daily"])
        self.assertEqual(report["summary"]["gone"], ["silver.nav_snapshots"])
        self.assertIn("silver.nav_snapshots is no longer in the database", report["summary"]["attention"])
        widened = RETURN_COLUMNS[:3] + [["excess_return", "double precision"]] + RETURN_COLUMNS[3:]
        rows = [(s, d, r, None, q, v, g) for s, d, r, q, v, g in self.before_returns]
        report = report_tool.build_report(source("a", returns(self.before_returns)),
                                          source("b", returns(rows, widened)))
        table = report["tables"]["gold.performance_comparison_daily"]
        self.assertEqual(table["status"], "changed")
        self.assertEqual(table["schema"], {"columns_added": ["excess_return"]})
        self.assertIn("table definition changed", report_tool.render_markdown(report))

    def test_markdown_without_details_fits_a_pr_comment(self):
        after = [(s, d, r + 0.01, q, v, g) for s, d, r, q, v, g in self.before_returns]
        report = self.build(after)
        short = report_tool.render_markdown(report, details=False)
        self.assertNotIn("<details>", short)
        self.assertIn("Details by table are in the JSON report", short)
        self.assertIn("### Biggest changes", short)

    def test_different_row_security_needs_attention(self):
        report = self.build(self.before_returns, after_header={**HEADER, "all_tenants": True})
        self.assertTrue(report_tool.has_changes(report))
        self.assertIn("different row-level security", report["summary"]["attention"][0])
        self.assertEqual(report["after"]["rows_read"], "all tenants")

    def test_whole_row_table_without_a_key(self):
        columns = [["benchmark_id", "text"], ["level", "double precision"]]
        before = report_tool.encode_table("silver.synthetic", "silver", columns, [], [("A", 1.0), ("B", 2.0)])
        after = report_tool.encode_table("silver.synthetic", "silver", columns, [], [("A", 1.0), ("B", 2.5)])
        self.assertTrue(before["whole_row"])
        result = report_tool.compare_table(before, after, 1e-9, 1e-12)
        self.assertEqual((result["rows"]["added"], result["rows"]["removed"], result["rows"]["changed"]), (1, 1, 0))

    def test_frozen_snapshots_use_the_medallion_layers_rules(self):
        frozen = [["11111111-1111-1111-1111-111111111111", "frozen", "d1", "d1", "s1", "2026-09-30",
                   "2026-10-01T09:00:00+00:00"]]
        base = {"table": report_tool.FROZEN_TABLE, "frozen": frozen}
        added = frozen[:1] + [["22222222-2222-2222-2222-222222222222", "frozen", "d2", "d2", "s1", "2026-10-31",
                               "2026-11-01T09:00:00+00:00"]]
        check = report_tool.frozen_check(base, {**base, "frozen": added})
        self.assertEqual(check["problems"], [])
        self.assertEqual(check["notes"], ["reporting_snapshots: 1 rows created during the candidate's run"])
        superseded = [frozen[0][:1] + ["superseded"] + frozen[0][2:],
                      ["33333333-3333-3333-3333-333333333333", "frozen", "d3", "d3", "s1", "2026-09-30",
                       "2026-10-18T09:00:00+00:00"]]
        check = report_tool.frozen_check(base, {**base, "frozen": superseded})
        self.assertEqual(check["problems"], [])
        self.assertIn("superseded by a snapshot created during the candidate's run", check["notes"][0])
        rewritten = [frozen[0][:3] + ["other"] + frozen[0][4:]]
        check = report_tool.frozen_check(base, {**base, "frozen": rewritten})
        self.assertEqual(len(check["problems"]), 2)


class FileTests(unittest.TestCase):
    def test_snapshot_files_round_trip_and_command_line(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            before = [("U9400001", "2026-10-15", "USD", "1000.50", "a", 1)]
            after = [("U9400001", "2026-10-15", "USD", "1001.50", "a", 2)]
            for name, rows in (("before.jsonl.gz", before), ("after.jsonl", after)):
                counts = report_tool.write_snapshot(HEADER, iter([nav(rows)]), folder / name)
                self.assertEqual(counts, {"silver.nav_snapshots": 1})
                self.assertEqual((folder / name).stat().st_mode & 0o777, 0o600)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = report_tool.main(["compare", "--before", str(folder / "before.jsonl.gz"),
                                         "--after", str(folder / "after.jsonl"),
                                         "--markdown", str(folder / "r.md"), "--json", str(folder / "r.json")])
            self.assertEqual(code, 1)
            saved = json.loads((folder / "r.json").read_text(encoding="utf-8"))
            self.assertAlmostEqual(saved["tables"]["silver.nav_snapshots"]["columns"]["total_text"]["max_abs"]["delta"],
                                   1.0)
            self.assertIn("**1 of 1 tables changed; 0 unchanged.**", output.getvalue())
            self.assertNotIn("<details>", output.getvalue())  # stdout has the summary only
            self.assertIn("<details>", (folder / "r.md").read_text(encoding="utf-8"))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(report_tool.main(["compare", "--before", str(folder / "after.jsonl"),
                                                   "--after", str(folder / "after.jsonl"),
                                                   "--markdown", str(folder / "s.md"),
                                                   "--json", str(folder / "s.json")]), 0)
            (folder / "other.json").write_text('{"something": "else"}\n', encoding="utf-8")
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                code = report_tool.main(["compare", "--before", str(folder / "other.json"),
                                         "--after", str(folder / "after.jsonl")])
            self.assertEqual(code, 2)
            self.assertIn("is not a release data snapshot", errors.getvalue())

    def test_table_filter(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "s.jsonl"
            report_tool.write_snapshot(HEADER, iter([returns([]), nav([])]), path)
            _, tables = report_tool.file_source(path, ["nav_snapshots"])
            self.assertEqual([entry["table"] for entry in tables], ["silver.nav_snapshots"])

    def test_catalog_lists_every_silver_and_gold_table(self):
        catalog = json.loads((ROOT / "infra" / "medallion" / "catalog.json").read_text(encoding="utf-8"))["postgres"]
        expected = sorted(f"{entry['layer']}.{key.split('.', 1)[1]}" for key, entry in catalog.items()
                          if entry["layer"] in ("silver", "gold") and entry["kind"] == "table")
        self.assertEqual([item["table"] for item in report_tool.catalog_tables()], expected)
        self.assertTrue(report_tool.catalog_views())


if __name__ == "__main__":
    unittest.main()
