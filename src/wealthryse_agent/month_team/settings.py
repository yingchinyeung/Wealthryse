"""Team settings, read from the environment. Nothing here is a secret."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import ModelSettings, _env, _int

REPORT_PROCESSING_SCOPE = "apac-data-zone"
MAX_REVIEW_ROUNDS = 2
MAX_STEP_ATTEMPTS = 3


@dataclass(frozen=True)
class TeamSettings:
    storage_account_url: str
    container: str
    monthly_token_budget: int
    report_time_limit_seconds: int
    page_image_dpi: int = 110
    # On only together with the package job's team-commentary setting: the
    # team's agreed commentary then goes into the month's decks (never the
    # factsheets).
    live: bool = False
    # Per-account decks are no longer built (Ying, 14 October 2026): with this
    # off the team checks the factsheet only, ignores any deck, and writes no
    # commentary, as the factsheet never carries it.
    decks: bool = False

    @classmethod
    def from_env(cls) -> "TeamSettings":
        return cls(
            storage_account_url=_env("AZURE_STORAGE_ACCOUNT_URL").rstrip("/"),
            container=_env("INTERNAL_REPORT_CONTAINER", "internal-reports"),
            monthly_token_budget=_int("AGENT_MONTH_TEAM_TOKEN_BUDGET", 20_000_000, 100_000, 500_000_000),
            report_time_limit_seconds=_int("AGENT_MONTH_TEAM_REPORT_SECONDS", 900, 60, 3600),
            page_image_dpi=_int("AGENT_MONTH_TEAM_PAGE_DPI", 110, 50, 200),
            live=_env("AGENT_MONTH_TEAM_LIVE", "false").lower() in ("1", "true", "yes"),
        )

    def validate(self) -> None:
        if not self.storage_account_url.startswith("https://"):
            raise ValueError("AZURE_STORAGE_ACCOUNT_URL must be the private report storage account")


def validate_report_model(settings: ModelSettings) -> None:
    """Report content is processed in the APAC Data Zone only; fail closed otherwise."""
    settings.validate()
    if settings.processing_scope.lower() != REPORT_PROCESSING_SCOPE:
        raise ValueError("The month-end team must use the APAC Data Zone report deployment")
