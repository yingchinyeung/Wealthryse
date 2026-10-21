"""The certified-figures register in code (medallion upgrade U5).

docs/MEDALLION_CERTIFIED_FIGURES.md explains the layer. The register is
infra/medallion/certified-figures.json, read on first use from the first of these that has it:
the folder named by MEDALLION_DIR, the repository's infra/medallion, /app/medallion (where the
platform image carries infra/medallion), and last the copy packaged with ibkr_etl
(certified_figures_register.json), so every image has it. The website's release ships only the
.py files of ibkr_etl, so it reads the same register from certified_figures_register_data.py,
used when the packaged JSON is missing too. After changing the register, refresh both copies with
`python -m ibkr_etl.certified_figures --write` (a test checks they match).
Other code can use medallion_dir() for the catalog and contracts, which are not packaged.

Nothing here reads client data or switches anything on, and importing it reads no file, so it
is safe to import everywhere. The stable API for the build, the website readers and the agents:

    medallion_dir() -> Path                 where the catalog and contracts are (and the register)
    register_path() -> Path                 the register file read, as above
    figure(figure_id) -> Figure             one figure; UnknownFigure (a KeyError) names close ids
    figures() -> dict[str, Figure]          all of them, in register order
    figures_for_page(page_id) -> list       the figures a page shows: its own, then borrowed ones
    pages() -> dict[str, Page]              the nine pages, in move order
    page(page_id) -> Page
    batch_for_page(page_id) -> Batch        (number, date) of the batch the page moves in
    page_modes(text) -> dict[page, mode]    parses CERTIFIED_FIGURES_PAGES ("overview=on,cash=shadow");
                                            every page is "off" unless listed with shadow or on
    served_figures(modes) -> list[Figure]   figures whose own page is "on" (what the read tool serves)
    period_allowed(figure, period) -> bool  whether "ytd", "year_2025", "month_2026-10" fits the figure
    definition_version(guide_name) -> str   first 12 hex of sha256 over the guide entry's wording
    calculation_version(figure, source_version=None) -> str
    freshness(as_of, latest, *, reason=None) -> dict   fresh, behind or late (read time)
    closest(figure_id, n=3) -> list[str]

A Figure carries id, label, guide (the Metrics guide entry), definition_id (its stable
anchor), definition_version, unit, kind, page, batch, method, source, inputs, field, item,
periods, per_benchmark, currency ("own" or None), basis, note and guide_gap. Labels are the
guide entry's name, except where two figures share an entry (open question 4): then a name
from the id, with the basis in brackets, such as "Maximum drawdown (every NAV date)".

Settings, all off unless set (read by the build, the website and the agents, not here):
BUILD_SETTING, PAGES_SETTING and AGENT_SETTING.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from . import metrics_guide

REGISTER_NAME = "certified-figures.json"
DIR_SETTING = "MEDALLION_DIR"
REPOSITORY_DIR = Path(__file__).resolve().parents[2] / "infra" / "medallion"
IMAGE_DIR = Path("/app/medallion")
PACKAGED_REGISTER = Path(__file__).with_name("certified_figures_register.json")
# The register as code, for the website: its release ships only .py files (deploy/website_release.py).
PACKAGED_MODULE = Path(__file__).with_name("certified_figures_register_data.py")

BUILD_SETTING = "CERTIFIED_FIGURES_BUILD"
PAGES_SETTING = "CERTIFIED_FIGURES_PAGES"
AGENT_SETTING = "AGENT_CERTIFIED_FIGURES"
MODES = ("off", "shadow", "on")
UNITS = ("ratio", "number", "money", "date", "text")
KINDS = ("scalar", "per_period", "per_item", "series", "per_period_item")
# Kinds whose rows carry a period (per_period_item also carries an item).
PERIOD_KINDS = ("per_period", "per_period_item")
IMPORTED = "imported"
# The source contracts promise data daily by 10:00 UTC and at most four days behind.
LATE_AFTER_BUSINESS_DAYS = 4
FRESHNESS_PROMISE = "daily by 10:00 UTC, at most 4 days behind"

_PERIOD_PATTERNS = {
    "mtd": r"mtd", "qtd": r"qtd", "ytd": r"ytd", "since_inception": r"since_inception",
    "year_YYYY": r"year_\d{4}", "month_YYYY-MM": r"month_\d{4}-(0[1-9]|1[0-2])",
}


class UnknownFigure(KeyError):
    """A figure id the register does not have; .closest lists the nearest ids."""

    def __init__(self, figure_id: str, nearest: list[str]):
        super().__init__(figure_id)
        self.figure_id = figure_id
        self.closest = nearest

    def __str__(self) -> str:
        hint = f"; did you mean {', '.join(self.closest)}?" if self.closest else ""
        return f"no certified figure {self.figure_id!r}{hint}"


@dataclass(frozen=True)
class Figure:
    id: str
    label: str
    guide: str
    definition_id: str
    definition_version: str
    unit: str
    kind: str
    page: str
    batch: int
    method: str
    source: str
    inputs: tuple[str, ...]
    field: str | None
    item: str | None
    periods: tuple[str, ...]
    per_benchmark: bool
    currency: str | None
    basis: str | None
    note: str | None
    guide_gap: str | None

    @property
    def link(self) -> str:
        return f"/metrics-guide#{self.definition_id}"

    @property
    def source_tables(self) -> tuple[str, ...]:
        """The gold or silver tables the figure is worked out from."""
        return self.inputs if self.source == "new" else (self.source, *self.inputs)


@dataclass(frozen=True)
class Batch:
    number: int
    date: date


@dataclass(frozen=True)
class Page:
    id: str
    order: int
    batch: int
    title: str
    routes: tuple[str, ...]
    apis: tuple[str, ...]
    guide_locations: tuple[str, ...]
    shows_from_other_pages: tuple[str, ...]
    deferred_here: tuple[str, ...]


def medallion_dir(environ: Mapping[str, str] | None = None) -> Path:
    """MEDALLION_DIR when set; otherwise the repository's infra/medallion, then the image's
    /app/medallion, whichever exists first (the repository's when neither does)."""
    environ = os.environ if environ is None else environ
    explicit = str(environ.get(DIR_SETTING, "")).strip()
    if explicit:
        return Path(explicit)
    return next((path for path in (REPOSITORY_DIR, IMAGE_DIR) if path.is_dir()), REPOSITORY_DIR)


def register_path(environ: Mapping[str, str] | None = None) -> Path:
    """The register in MEDALLION_DIR, the repository's infra/medallion or /app/medallion, the first
    that has it; otherwise the JSON copy packaged with ibkr_etl, or, where that is missing too (the
    website), the copy as code."""
    environ = os.environ if environ is None else environ
    explicit = str(environ.get(DIR_SETTING, "")).strip()
    folders = ([Path(explicit)] if explicit else []) + [REPOSITORY_DIR, IMAGE_DIR]
    return next((folder / REGISTER_NAME for folder in folders if (folder / REGISTER_NAME).is_file()),
                PACKAGED_REGISTER if PACKAGED_REGISTER.is_file() else PACKAGED_MODULE)


def register_text(environ: Mapping[str, str] | None = None) -> str:
    """The register's text, from register_path()."""
    path = register_path(environ)
    if path == PACKAGED_MODULE:
        from . import certified_figures_register_data as packaged

        return packaged.TEXT
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def register() -> dict:
    """The register as parsed JSON, read once (shared; do not change it)."""
    return json.loads(register_text())


def reload() -> None:
    """Forget the register read so far (tests, or after MEDALLION_DIR changes)."""
    for cached in (register, _figures, _pages):
        cached.cache_clear()


def definition_version(guide_name: str) -> str:
    """First 12 hex characters of a sha256 over the guide entry's name, meaning, calculation and
    interpretation, so any change to its wording changes the version."""
    entry = metrics_guide.definition(guide_name)
    text = "\n".join((entry.name, entry.meaning, entry.calculation, entry.interpretation))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _label(figure_id: str, raw: Mapping, shared: bool) -> str:
    if not shared:
        return raw["guide"]
    words = figure_id.split(".", 1)[1].replace("_", " ")
    label = words[:1].upper() + words[1:]
    return f"{label} ({raw['basis']})" if raw.get("basis") else label


@lru_cache(maxsize=1)
def _figures() -> dict[str, Figure]:
    data = register()
    pages = data["pages"]
    uses: dict[str, int] = {}
    for raw in data["figures"].values():
        uses[raw["guide"]] = uses.get(raw["guide"], 0) + 1
    found = {}
    for figure_id, raw in data["figures"].items():
        found[figure_id] = Figure(
            id=figure_id,
            label=_label(figure_id, raw, uses[raw["guide"]] > 1),
            guide=raw["guide"],
            definition_id=metrics_guide.metric_anchor(raw["guide"]),
            definition_version=definition_version(raw["guide"]),
            unit=raw["unit"],
            kind=raw["kind"],
            page=raw["page"],
            batch=int(pages[raw["page"]]["batch"]),
            method=raw["method"],
            source=raw["source"],
            inputs=tuple(raw.get("inputs", ())),
            field=raw.get("field"),
            item=raw.get("item"),
            periods=tuple(raw.get("periods", ())),
            per_benchmark=bool(raw.get("per_benchmark")),
            currency=raw.get("currency"),
            basis=raw.get("basis"),
            note=raw.get("note"),
            guide_gap=raw.get("guide_gap"),
        )
    return found


def figures() -> dict[str, Figure]:
    return dict(_figures())


def closest(figure_id: str, n: int = 3) -> list[str]:
    return difflib.get_close_matches(str(figure_id), list(_figures()), n=n, cutoff=0.5)


def figure(figure_id: str) -> Figure:
    try:
        return _figures()[figure_id]
    except KeyError:
        raise UnknownFigure(str(figure_id), closest(figure_id)) from None


@lru_cache(maxsize=1)
def _pages() -> dict[str, Page]:
    found = {}
    for page_id, raw in sorted(register()["pages"].items(), key=lambda item: item[1]["order"]):
        found[page_id] = Page(
            id=page_id, order=int(raw["order"]), batch=int(raw["batch"]), title=raw["title"],
            routes=tuple(raw["routes"]), apis=tuple(raw["apis"]),
            guide_locations=tuple(raw["guide_locations"]),
            shows_from_other_pages=tuple(raw.get("shows_from_other_pages", ())),
            deferred_here=tuple(raw.get("deferred_here", ())))
    return found


def pages() -> dict[str, Page]:
    return dict(_pages())


def page(page_id: str) -> Page:
    try:
        return _pages()[page_id]
    except KeyError:
        raise KeyError(f"no certified page {page_id!r}; pages are {', '.join(_pages())}") from None


def batch_for_page(page_id: str) -> Batch:
    number = page(page_id).batch
    return Batch(number, date.fromisoformat(register()["batches"][str(number)]["date"]))


def figures_for_page(page_id: str) -> list[Figure]:
    """The page's own figures in register order, then the ones it shows from other pages."""
    shown = page(page_id).shows_from_other_pages
    own = [item for item in _figures().values() if item.page == page_id]
    return own + [figure(figure_id) for figure_id in shown]


def page_modes(text: str | None) -> dict[str, str]:
    """{page: off|shadow|on} from a CERTIFIED_FIGURES_PAGES value. Unknown pages and modes are
    ignored, so a typo leaves that page off."""
    modes = {page_id: "off" for page_id in _pages()}
    for part in str(text or "").split(","):
        name, _, mode = part.partition("=")
        name, mode = name.strip().lower(), mode.strip().lower()
        if name in modes and mode in MODES:
            modes[name] = mode
    return modes


def served_figures(modes: Mapping[str, str]) -> list[Figure]:
    """Figures whose own page is on: the only ones the read tool may serve."""
    return [item for item in _figures().values() if modes.get(item.page) == "on"]


def period_allowed(item: Figure, period: str) -> bool:
    if item.kind not in PERIOD_KINDS:
        return period in ("", None)
    return any(re.fullmatch(_PERIOD_PATTERNS[template], str(period or "")) for template in item.periods)


def calculation_version(item: Figure, source_version: str | None = None) -> str:
    """The figure's method, joined with the source row's own version where it has one:
    'risk-analytics-v2+benchmark-comparison-v1'. IBKR's values copied unchanged say
    'imported+<source contract version>'. A source version that is the method itself is not repeated."""
    parts = [str(source_version)] if source_version and str(source_version) != item.method else []
    return "+".join([IMPORTED, *parts] if item.method == IMPORTED else [*parts, item.method])


def business_days_between(earlier: date, later: date) -> int:
    """Weekdays after earlier, up to and including later (0 when later is not after earlier)."""
    days, current = 0, earlier
    while current < later:
        current += timedelta(days=1)
        days += current.weekday() < 5
    return days


def freshness(as_of: date | str, latest: date | str | None, *, reason: str | None = None) -> dict:
    """fresh when the figure is for the subject's newest validated NAV date; behind when a newer
    date has no published figures yet; late when that is more than the promised four business
    days."""
    as_of = date.fromisoformat(str(as_of)[:10])
    latest = as_of if latest is None else date.fromisoformat(str(latest)[:10])
    behind = business_days_between(as_of, latest)
    if latest <= as_of:
        status = "fresh"
    elif behind > LATE_AFTER_BUSINESS_DAYS:
        status = "late"
    else:
        status = "behind"
    result = {"status": status, "latest_data_date": latest.isoformat(), "business_days_behind": behind,
              "promise": FRESHNESS_PROMISE}
    if status != "fresh" and reason:
        result["reason"] = reason
    return result


MODULE_HEAD = (
    '"""The certified figures register as code: generated, do not edit.\n\n'
    "A copy of infra/medallion/certified-figures.json for the website, whose release ships only the .py\n"
    "files of ibkr_etl. Refresh it with python -m ibkr_etl.certified_figures --write (a test keeps the\n"
    'copies equal)."""\n'
)


def module_source(text: str) -> str:
    """certified_figures_register_data.py for a register text (a raw string holding it unchanged)."""
    if "'''" in text or text.endswith("\\"):
        raise ValueError("the register text cannot sit in a raw triple-quoted string")
    return MODULE_HEAD + "TEXT = r'''" + text + "'''\n"


def main(argv: list[str] | None = None) -> int:
    """--write copies infra/medallion/certified-figures.json over the packaged copies (the JSON and
    the copy as code); without it, says whether they match."""
    import argparse

    parser = argparse.ArgumentParser(description="Keep the packaged register copies in step.")
    parser.add_argument("--write", action="store_true", help="Refresh the packaged copies from infra/medallion.")
    args = parser.parse_args(argv)
    source = REPOSITORY_DIR / REGISTER_NAME
    text = source.read_text(encoding="utf-8")
    copies = {PACKAGED_REGISTER: source.read_bytes(), PACKAGED_MODULE: module_source(text).encode("utf-8")}
    differ = [path for path, data in copies.items() if not path.is_file() or path.read_bytes() != data]
    if args.write and differ:
        for path in differ:
            path.write_bytes(copies[path])
            print(f"Wrote {path.name} from {source}.")
        return 0
    print("The packaged register copies match." if not differ
          else "The packaged register copies differ (%s); run with --write." % ", ".join(p.name for p in differ))
    return 0 if not differ else 1


if __name__ == "__main__":
    raise SystemExit(main())
