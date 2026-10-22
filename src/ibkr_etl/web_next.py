"""WealthRyse Next: the redesigned website, behind the WEB_DESIGN switch.

WEB_DESIGN has three positions:

* classic (the default): every page is served exactly as before.
* preview: owners see a "Try the new design" link and can turn the new design on
  for their own browser. Everyone else keeps the classic design.
* next: everyone gets the new design. The classic design is retired: there is
  no way back to it in the browser, and a saved classic choice is ignored.
  Setting WEB_DESIGN=classic remains the site-wide rollback.

The choice is kept in the wr_design cookie. The new design keeps the classic
visual language (black and white, Barlow capitals, square corners, the same light
and dark themes) and changes how pages are organised: a home briefing, a side
rail, search, figure details and a phone layout. It works in the browser only. It reads the same endpoints the classic
pages already call, so no figure, calculation, data route or web address changes.
"""

from __future__ import annotations

import json
import os
import re
from html import escape
from urllib.parse import quote

from .web_next_pages import PAGES_CSS, PAGES_SCRIPT
from .web_next_perf import PERF_CSS, PERF_SCRIPT
from .web_next_ui import NEXT_CSS, NEXT_SCRIPT

COOKIE = "wr_design"
COOKIE_MAX_AGE = 180 * 24 * 3600
SETTINGS = ("classic", "preview", "next")

# Speculation rules: start loading an account page when someone points at its link.
SPECULATION_RULES = json.dumps({
    "prefetch": [{
        "source": "document",
        "where": {"or": [
            {"href_matches": "/accounts/*"},
            {"href_matches": "/"},
            {"href_matches": "/combined-holdings"},
            {"href_matches": "/metrics-guide"},
        ]},
        "eagerness": "moderate",
    }]
})

_HTML_TAG = re.compile(r"<html\b([^>]*)>", re.IGNORECASE)
_SESSION = '<div class="session">'


def setting() -> str:
    value = os.environ.get("WEB_DESIGN", "classic").strip().lower()
    return value if value in SETTINGS else "classic"


def design_for(cookie: str | None, role: str | None) -> str:
    """The design this person sees: "next" or "classic"."""
    mode = setting()
    if mode == "next":
        return "next"
    if mode == "preview" and role == "owner" and cookie == "next":
        return "next"
    return "classic"


def may_choose(design: str, role: str | None) -> bool:
    """Whether this person may pick this design for their own browser."""
    mode = setting()
    if design not in ("next", "classic") or mode == "classic":
        return False
    if design == "classic":
        return mode == "preview"
    return mode == "next" or role == "owner"


# Phases of the page-by-page redesign built so far. Each one is released on its own.
PAGES_PHASES = 2
_PAGES_WORDS = {"off": (0, 0), "preview": (0, 1), "on": (1, 1)}


def pages_levels() -> tuple[int, int]:
    """WEB_NEXT_PAGES as (phases for everyone, phases for owners).

    off, preview and on are the phase 1 settings. "1/2" gives everyone phase 1 and
    owners phases 1 and 2; "2" gives everyone phases 1 and 2. Anything else is off.
    """
    value = os.environ.get("WEB_NEXT_PAGES", "off").strip().lower()
    if value in _PAGES_WORDS:
        return _PAGES_WORDS[value]
    match = re.fullmatch(r"(\d+)(?:/(\d+))?", value)
    if not match:
        return 0, 0
    live = min(int(match.group(1)), PAGES_PHASES)
    owners = min(max(int(match.group(2) or live), live), PAGES_PHASES)
    return live, owners


def pages_setting() -> str:
    """WEB_NEXT_PAGES in words: off, preview (owners only) or on (everyone has something)."""
    live, owners = pages_levels()
    return "on" if live else "preview" if owners else "off"


def pages_level(role: str | None) -> int:
    """How many phases of the page-by-page redesign this person gets (it needs the new design too)."""
    live, owners = pages_levels()
    return owners if role == "owner" else live


def pages_for(role: str | None) -> bool:
    """Whether this person gets any of the page-by-page redesign."""
    return pages_level(role) >= 1


def safe_return_path(value: str | None) -> str:
    """Only paths on this site, so the switch can't be used to send people elsewhere."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


def _try_link(back: str) -> str:
    href = "/design/next?back=" + quote(back, safe="")
    return (f'<a class="wr-try-next" href="{escape(href)}" '
            'style="margin-right:10px">Try the new design</a>')


def transform(html: str, *, design: str, role: str | None, back: str, page_scripts: dict[str, str],
              metric_anchors: dict[str, str], pages: bool | int | None = None) -> str:
    """Apply the chosen design to a rendered page."""
    if design != "next":
        if setting() == "preview" and role == "owner" and _SESSION in html:
            html = html.replace(_SESSION, _SESSION + _try_link(back), 1)
        return html
    if not _HTML_TAG.search(html) or "</head>" not in html or "</body>" not in html:
        return html
    html = _HTML_TAG.sub(lambda m: f'<html{m.group(1)} data-design="next">', html, count=1)
    # Scripts the new design replaces, such as the home page's account cards.
    for script in page_scripts.values():
        html = html.replace(script, "")
    config = {
        "role": role or "viewer",
        "canClassic": may_choose("classic", role),
        "metrics": metric_anchors,
    }
    level = pages_level(role) if pages is None else PAGES_PHASES if pages is True else int(pages or 0)
    head = (f'<style id="nx-css">{NEXT_CSS}</style>'
            + (f'<style id="nx-pages-css">{PAGES_CSS}</style>' if level >= 1 else '')
            + (f'<style id="nx-perf-css">{PERF_CSS}</style>' if level >= 2 else '')
            + f'<script type="speculationrules">{SPECULATION_RULES}</script>')
    marker = html.find("</head>")
    html = html[:marker] + head + html[marker:]
    body = ("<script>window.WR_NEXT=" + json.dumps(config).replace("</", "<\\/") + ";"
            + NEXT_SCRIPT + "</script>"
            + (f"<script>{PAGES_SCRIPT}</script>" if level >= 1 else "")
            + (f"<script>{PERF_SCRIPT}</script>" if level >= 2 else ""))
    marker = html.rfind("</body>")
    return html[:marker] + body + html[marker:]
