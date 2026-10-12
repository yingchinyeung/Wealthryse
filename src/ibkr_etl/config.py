from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PROJECT_ROOT = Path(
    os.environ.get("IBKR_PROJECT_ROOT", Path(__file__).resolve().parents[2])
).resolve()


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_path(value: str, root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (root / path).resolve()


@dataclass(frozen=True)
class Settings:
    project_root: Path
    flex_token: str
    flex_query_id: str
    database_path: Path
    raw_report_dir: Path
    log_dir: Path
    timezone_name: str
    daily_lookback_days: int
    user_agent: str
    request_timeout_seconds: int
    max_wait_seconds: int
    flex_min_send_interval_seconds: int = 10
    flex_transient_cooldown_seconds: int = 1800
    database_url: str = ""
    tenant_id: str = "00000000-0000-0000-0000-000000000001"
    tenant_name: str = "Own Portfolio"
    azure_storage_account_url: str = ""
    azure_raw_container: str = "ibkr-raw"
    azure_report_container: str = "ibkr-reports"

    @property
    def database_backend(self) -> str:
        return "postgresql" if self.database_url.startswith(("postgres://", "postgresql://")) else "sqlite"

    @property
    def timezone(self) -> tzinfo:
        try:
            return ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError:
            # Windows Python installations do not always bundle the IANA timezone
            # database. Hong Kong has used UTC+08:00 without DST since 1979, so this
            # fallback preserves the intended scheduling dates without a dependency.
            if self.timezone_name == "Asia/Hong_Kong":
                return timezone(timedelta(hours=8), name="Asia/Hong_Kong")
            raise

    def require_flex_credentials(self) -> None:
        missing = []
        if not self.flex_token:
            missing.append("IBKR_FLEX_TOKEN")
        if not self.flex_query_id:
            missing.append("IBKR_FLEX_QUERY_ID")
        if missing:
            joined = ", ".join(missing)
            raise ValueError(
                f"Missing {joined}. Add the value(s) to {self.project_root / '.env'}."
            )


def load_settings(env_path: Path | None = None) -> Settings:
    path = env_path or PROJECT_ROOT / ".env"
    file_values = _read_env_file(path)

    def get(name: str, default: str = "") -> str:
        return os.environ.get(name, file_values.get(name, default))

    root = path.resolve().parent
    return Settings(
        project_root=root,
        flex_token=get("IBKR_FLEX_TOKEN"),
        flex_query_id=get("IBKR_FLEX_QUERY_ID"),
        database_path=_resolve_path(get("DATABASE_PATH", "data/portfolio.db"), root),
        raw_report_dir=_resolve_path(get("RAW_REPORT_DIR", "raw/flex"), root),
        log_dir=_resolve_path(get("LOG_DIR", "logs"), root),
        timezone_name=get("PORTFOLIO_TIMEZONE", "Asia/Hong_Kong"),
        daily_lookback_days=max(1, int(get("DAILY_LOOKBACK_DAYS", "7"))),
        user_agent=get("FLEX_USER_AGENT", "IBKR-Portfolio-ETL/1.0"),
        request_timeout_seconds=max(10, int(get("FLEX_REQUEST_TIMEOUT_SECONDS", "60"))),
        max_wait_seconds=max(20, int(get("FLEX_MAX_WAIT_SECONDS", "300"))),
        flex_min_send_interval_seconds=max(
            6, int(get("FLEX_MIN_SEND_INTERVAL_SECONDS", "10"))
        ),
        flex_transient_cooldown_seconds=max(
            60, int(get("FLEX_TRANSIENT_COOLDOWN_SECONDS", "1800"))
        ),
        database_url=get("DATABASE_URL"),
        tenant_id=get("PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"),
        tenant_name=get("PORTFOLIO_TENANT_NAME", "Own Portfolio"),
        azure_storage_account_url=get("AZURE_STORAGE_ACCOUNT_URL"),
        azure_raw_container=get("AZURE_RAW_CONTAINER", "ibkr-raw"),
        azure_report_container=get("AZURE_REPORT_CONTAINER", "ibkr-reports"),
    )
