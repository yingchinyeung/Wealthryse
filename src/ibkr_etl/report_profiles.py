"""Which report design a subject uses, and the identity its reports show.

Alpha World MS and Alpha World T have their own factsheet and presentation
designs. A fixed-unit composite opened in the portfolio builder can use either
design under its own name, with its own description and a composition tree in
place of the MS or T allocation diagram. Its numbers follow the design's
methodology: HKD, average 1M HIBOR as the risk-free rate, the design's market
session calendars and the composite's own inception.

MS and T keep their built-in identity, so every rule that names them still
applies to them unchanged. Everything here is pure; the profile of a composite
is written once, when the owner approves its terms, into its platform subject's
metadata, and read back with its fingerprint checked.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

from .composition_tree import composite_tree
from .subject_onboarding import configuration_sha256

PROFILE_VERSION = 1
# The approved MS and T methodology, as the analytics modules apply it.
STYLES: dict[str, dict[str, Any]] = {
    "alpha-world-ms": {"label": "Alpha World MS", "markets": ("XNYS", "XHKG")},
    "alpha-world-t": {"label": "Alpha World T", "markets": ("XNYS", "XHKG", "XSHG", "XTAI")},
}
BUILT_IN_INCEPTION = "2025-01-20"
REPORTING_CURRENCY = "HKD"
NAME_LIMIT = 48


class ReportProfileError(ValueError):
    """A report profile is missing, malformed or no longer matches its subject."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReportProfile:
    subject_id: str
    style: str
    display_name: str
    inception: str
    reporting_currency: str
    markets: tuple[str, ...]
    # None for MS and T, whose own design already carries these.
    description: str | None = None
    composition: Mapping[str, Any] | None = None
    definition_sha256: str | None = None

    @property
    def built_in(self) -> bool:
        return self.subject_id == self.style

    def as_dict(self) -> dict[str, Any]:
        """What a frozen month-end snapshot carries; the renderers read only this."""

        return {
            "version": PROFILE_VERSION,
            "subject_id": self.subject_id,
            "style": self.style,
            "display_name": self.display_name,
            "description": self.description,
            "inception": self.inception,
            "reporting_currency": self.reporting_currency,
            "markets": list(self.markets),
            "composition": dict(self.composition) if self.composition is not None else None,
            "definition_sha256": self.definition_sha256,
        }


def built_in_profile(subject_id: str) -> ReportProfile | None:
    style = STYLES.get(subject_id)
    if style is None:
        return None
    return ReportProfile(subject_id=subject_id, style=subject_id, display_name=style["label"],
                         inception=BUILT_IN_INCEPTION, reporting_currency=REPORTING_CURRENCY,
                         markets=tuple(style["markets"]))


def style_choices() -> list[dict[str, str]]:
    return [{"style": key, "label": f"{value['label']} design"} for key, value in STYLES.items()]


def check_style(definition: Mapping[str, Any], style: str | None) -> str | None:
    """The design a new portfolio reports in; an HKD composite needs MS or T's.

    The standard design reports in USD, so an HKD composite without one of
    these designs would have no monthly reports.
    """

    composite = definition.get("subject_type") == "composite_portfolio"
    currency = str(definition.get("base_currency") or "")
    if style is None:
        if composite and currency == REPORTING_CURRENCY:
            raise ReportProfileError("An HKD composite reports in the Alpha World MS or T design; choose one")
        return None
    if style not in STYLES:
        raise ReportProfileError("Choose the Alpha World MS or Alpha World T report design")
    if not composite:
        raise ReportProfileError("Only fixed-unit composites can use the Alpha World report designs")
    if str(definition.get("subject_id") or "") in STYLES:
        raise ReportProfileError("Alpha World MS and T keep their own reports")
    if currency != REPORTING_CURRENCY:
        raise ReportProfileError("The Alpha World report designs report in HKD; set the reporting currency to HKD")
    from .factsheet_tree import layout
    try:
        layout(composite_tree(title=str(definition.get("display_name") or ""), base_currency=currency,
                              constant_cash=definition.get("constant_cash", 0),
                              sleeves=list(definition.get("components") or [])))
    except ValueError as error:
        raise ReportProfileError(f"The factsheet cannot draw this composition: {error}. "
                                 "Use shorter sleeve names or fewer sleeves") from error
    return style


def _check_printable(display_name: str, description: str) -> None:
    """The factsheet prints the name in the design's embedded fonts, which carry plain ASCII only, and the
    description in the About column."""

    if not (display_name.isascii() and display_name.isprintable()):
        raise ReportProfileError("Use plain letters, digits and punctuation in the portfolio name; "
                                 "the report fonts carry no accents or symbols")
    from reportlab.pdfbase.pdfmetrics import stringWidth
    if len(display_name) > NAME_LIMIT or stringWidth(display_name, "Helvetica-Bold", 5.5) > 171.0:
        raise ReportProfileError(f"Keep the portfolio name to {NAME_LIMIT} characters; reports print it in the "
                                 "factsheet title and KPI panel")
    try:
        description.encode("cp1252")
    except UnicodeEncodeError as error:
        raise ReportProfileError("The description uses a character the report fonts cannot print; "
                                 "use plain text") from error
    from .profiled_factsheet import about_lines
    try:
        about_lines(description)
    except ValueError as error:
        raise ReportProfileError(str(error)) from error


def build_profile(*, definition: Mapping[str, Any], style: str, description: str) -> dict[str, Any]:
    """The profile record for a composite, stored in its platform subject's metadata.

    The definition is the exact approved fixed-unit composite; its fingerprint
    binds the profile to it, so a changed definition needs a new approval.
    """

    if check_style(definition, style) is None:
        raise ReportProfileError("Choose the Alpha World MS or Alpha World T report design")
    currency = str(definition["base_currency"])
    text = " ".join(str(description or "").split())
    if not 10 <= len(text) <= 600:
        raise ReportProfileError("Describe the portfolio in 10 to 600 characters; its reports show this text")
    inception = date.fromisoformat(str(definition.get("normalization_date") or "")).isoformat()
    display_name = str(definition.get("display_name") or "").strip()
    if not display_name:
        raise ReportProfileError("The portfolio needs a display name")
    _check_printable(display_name, text)
    tree = composite_tree(title=display_name, base_currency=currency,
                          constant_cash=definition.get("constant_cash", 0),
                          sleeves=list(definition.get("components") or []))
    record = {
        "version": PROFILE_VERSION,
        "style": style,
        "display_name": display_name,
        "description": text,
        "inception": inception,
        "reporting_currency": currency,
        "markets": list(STYLES[style]["markets"]),
        "composition": tree,
        "definition_sha256": configuration_sha256(definition),
    }
    return {**record, "profile_sha256": _sha256(record)}


def profile_from_metadata(subject_id: str, metadata: Mapping[str, Any] | str | None, *,
                          definition_sha256: str | None = None) -> ReportProfile | None:
    """The subject's profile: MS and T built in, a composite from its stored record, else None.

    When the caller knows the fingerprint of the subject's current approved
    definition, the profile must describe exactly that definition.
    """

    built_in = built_in_profile(subject_id)
    if built_in is not None:
        return built_in
    if isinstance(metadata, str):
        metadata = json.loads(metadata or "{}")
    record = dict((metadata or {}).get("report_profile") or {})
    if not record:
        return None
    stored = record.pop("profile_sha256", None)
    if stored != _sha256(record):
        raise ReportProfileError(f"The report profile of {subject_id} fails its integrity check")
    if record.get("version") != PROFILE_VERSION or record.get("style") not in STYLES:
        raise ReportProfileError(f"The report profile of {subject_id} uses an unknown design")
    if definition_sha256 is not None and record.get("definition_sha256") != definition_sha256:
        raise ReportProfileError(f"The report profile of {subject_id} describes a different definition")
    style = str(record["style"])
    if list(record.get("markets") or []) != list(STYLES[style]["markets"]):
        raise ReportProfileError(f"The report profile of {subject_id} uses other market calendars")
    return ReportProfile(
        subject_id=subject_id, style=style, display_name=str(record["display_name"]),
        inception=date.fromisoformat(str(record["inception"])).isoformat(),
        reporting_currency=str(record["reporting_currency"]), markets=tuple(record["markets"]),
        description=str(record["description"]), composition=dict(record["composition"]),
        definition_sha256=str(record["definition_sha256"]),
    )


def load_profile(connection, subject_id: str, *, tenant_id: str | None = None) -> ReportProfile | None:
    """Read a subject's profile, bound to its current approved calculation definition."""

    built_in = built_in_profile(subject_id)
    if built_in is not None:
        return built_in
    clause, params = ("s.tenant_id = ? AND ", (tenant_id,)) if tenant_id else ("", ())
    row = connection.execute(
        "SELECT s.metadata_json, s.reporting_currency, d.configuration_sha256 FROM platform_subjects s "
        "LEFT JOIN subject_calculation_definitions d ON d.tenant_id = s.tenant_id AND d.subject_id = s.subject_id "
        "AND d.approval_status = 'approved' AND d.effective_to IS NULL "
        "WHERE " + clause + "s.subject_id = ?",
        params + (subject_id,),
    ).fetchone()
    if row is None:
        return None
    profile = profile_from_metadata(subject_id, row["metadata_json"],
                                    definition_sha256=str(row["configuration_sha256"] or "") or None)
    if profile is not None and (row["configuration_sha256"] is None
                                or str(row["reporting_currency"]) != profile.reporting_currency):
        raise ReportProfileError(f"{subject_id} has no approved definition in its report currency")
    return profile


def snapshot_profile(snapshot: Mapping[str, Any]) -> ReportProfile | None:
    """The profile a frozen snapshot carries, or MS and T's built-in one."""

    subject_id = str(snapshot.get("subject_id") or "")
    built_in = built_in_profile(subject_id)
    if built_in is not None:
        return built_in
    record = snapshot.get("report_profile")
    if not record:
        return None
    if record.get("subject_id") != subject_id or record.get("style") not in STYLES:
        raise ReportProfileError("The snapshot's report profile belongs to another subject or design")
    if list(record.get("markets") or []) != list(STYLES[record["style"]]["markets"]):
        raise ReportProfileError("The snapshot's report profile uses other market calendars")
    return ReportProfile(
        subject_id=subject_id, style=str(record["style"]), display_name=str(record["display_name"]),
        inception=str(record["inception"]), reporting_currency=str(record["reporting_currency"]),
        markets=tuple(record["markets"]), description=record.get("description"),
        composition=record.get("composition"), definition_sha256=record.get("definition_sha256"),
    )


def report_style(snapshot_or_subject: Mapping[str, Any] | str) -> str | None:
    """'alpha-world-ms', 'alpha-world-t' or None for the standard account design."""

    if isinstance(snapshot_or_subject, str):
        profile = built_in_profile(snapshot_or_subject)
    else:
        profile = snapshot_profile(snapshot_or_subject)
    return profile.style if profile is not None else None
