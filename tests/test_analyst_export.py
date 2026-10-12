from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from ibkr_etl.analyst_export import build_csv_zip, export_snapshot


class AnalystExportTests(unittest.TestCase):
    def test_export_snapshot_and_csv_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            source = sqlite3.connect(":memory:")
            source.execute("CREATE TABLE sample(id INTEGER, amount REAL, name TEXT)")
            source.executemany(
                "INSERT INTO sample VALUES (?, ?, ?)",
                ((1, 10.5, "Alpha"), (2, None, "Beta")),
            )

            snapshot = tmp_path / "snapshot.sqlite"
            manifest = export_snapshot(
                source,
                snapshot,
                tenant_id="00000000-0000-0000-0000-000000000001",
                generated_at="2026-08-21T16:00:00+08:00",
                exports=(("sample", "SELECT * FROM sample ORDER BY id"),),
            )
            source.close()

            exported = sqlite3.connect(snapshot)
            try:
                self.assertEqual(
                    exported.execute("SELECT COUNT(*) FROM sample").fetchone()[0],
                    2,
                )
                self.assertEqual(
                    exported.execute(
                        "SELECT value FROM snapshot_metadata WHERE key='tenant_id'"
                    ).fetchone()[0],
                    "00000000-0000-0000-0000-000000000001",
                )
            finally:
                exported.close()

            self.assertEqual(manifest["row_counts"], {"sample": 2})
            self.assertEqual(len(manifest["sqlite_sha256"]), 64)

            csv_zip = tmp_path / "exports.zip"
            build_csv_zip(snapshot, csv_zip, manifest)
            with zipfile.ZipFile(csv_zip) as archive:
                self.assertIn("sample.csv", archive.namelist())
                self.assertIn("Alpha", archive.read("sample.csv").decode("utf-8"))
                self.assertEqual(
                    json.loads(archive.read("manifest.json"))["row_counts"]["sample"],
                    2,
                )


if __name__ == "__main__":
    unittest.main()
