from __future__ import annotations

import importlib.util
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from ibkr_etl.config import Settings


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "generate_daily_report.py"
SPEC = importlib.util.spec_from_file_location("generate_daily_report_script", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DashboardReferenceGuardTests(unittest.TestCase):
    def settings(self, root: Path) -> Settings:
        return Settings(
            project_root=root,
            flex_token="",
            flex_query_id="",
            database_path=root / "portfolio.db",
            raw_report_dir=root / "raw",
            log_dir=root / "logs",
            timezone_name="Asia/Hong_Kong",
            daily_lookback_days=7,
            user_agent="test",
            request_timeout_seconds=10,
            max_wait_seconds=20,
            database_url="postgresql://example.invalid/ibkr",
            tenant_id="00000000-0000-0000-0000-000000000001",
            azure_storage_account_url="https://example.blob.core.windows.net",
            azure_report_container="analytics-reports",
        )

    def test_restores_refreshes_and_persists_reference_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings = self.settings(Path(temporary_directory))
            upload = Mock(return_value="https://example/reference_data.json")
            with (
                patch.object(MODULE, "download_file_if_exists", return_value=True) as download,
                patch.object(
                    MODULE,
                    "refresh_reference_data",
                    return_value={"risk_free_available": True, "warnings": []},
                ) as refresh,
                patch.object(
                    MODULE,
                    "load_reference_data",
                    return_value={"risk_free": {"USD": {"annual_rate": 0.04}}},
                ),
                patch.object(MODULE, "upload_file", upload),
            ):
                result = MODULE.prepare_azure_reference_data(
                    settings, settings.database_path
                )

            self.assertTrue(result["risk_free_available"])
            self.assertEqual(1, download.call_count)
            self.assertEqual(0, refresh.call_args.kwargs["max_sector_lookups"])
            self.assertEqual(1, upload.call_count)

    def test_refuses_to_publish_when_risk_free_rate_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings = self.settings(Path(temporary_directory))
            upload = Mock()
            with (
                patch.object(MODULE, "download_file_if_exists", return_value=False),
                patch.object(
                    MODULE,
                    "refresh_reference_data",
                    return_value={
                        "risk_free_available": False,
                        "warnings": ["Risk-free refresh failed; cached value retained"],
                    },
                ),
                patch.object(
                    MODULE,
                    "load_reference_data",
                    return_value={"risk_free": {}},
                ),
                patch.object(MODULE, "upload_file", upload),
            ):
                with self.assertRaisesRegex(RuntimeError, "Refusing to publish"):
                    MODULE.prepare_azure_reference_data(
                        settings, settings.database_path
                    )

            upload.assert_not_called()

    def test_local_dashboard_generation_does_not_require_azure_reference_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings = replace(
                self.settings(Path(temporary_directory)),
                azure_storage_account_url="",
            )
            with patch.object(MODULE, "refresh_reference_data") as refresh:
                result = MODULE.prepare_azure_reference_data(
                    settings, settings.database_path
                )

            self.assertIsNone(result)
            refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
