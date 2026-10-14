"""The composition of a derived portfolio as a tree.

One model and one SVG drawing serve the portfolio builder, the onboarding
case page and, later, the portfolio's own reports, so all three always show
the same picture. Pure functions; nothing here reads the database.

A fixed-unit composite starts at a value of 100: each sleeve's exposure is
100 x its coefficient and the constant cash makes up the rest. The tree groups
sleeves into long, short and cash, with each one's share of the starting NAV.
"""

from __future__ import annotations

import math
from html import escape
from typing import Any, Mapping, Sequence

KIND_LABELS = {"account": "Account", "portfolio": "Portfolio", "benchmark": "Market index"}


def _number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a number") from error
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def composite_tree(*, title: str, base_currency: str, constant_cash: float,
                   sleeves: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Long, short and cash groups for a fixed-unit composite.

    Each sleeve needs name, kind (account, portfolio or benchmark), currency
    and coefficient, as in a composite definition's components.
    """

    cash = _number(constant_cash, "Constant cash")
    items = []
    for sleeve in sleeves:
        coefficient = _number(sleeve.get("coefficient"), "Coefficient")
        items.append({
            "name": str(sleeve.get("name") or sleeve.get("component_name") or sleeve.get("component_id") or ""),
            "kind": str(sleeve.get("kind") or sleeve.get("component_type") or ""),
            "currency": str(sleeve.get("currency") or sleeve.get("source_currency") or ""),
            "exposure": 100.0 * coefficient,
        })
    nav = cash + sum(item["exposure"] for item in items)
    if nav <= 0:
        raise ValueError("The starting NAV must be positive")
    long = sum(item["exposure"] for item in items if item["exposure"] > 0)
    short = -sum(item["exposure"] for item in items if item["exposure"] < 0)

    def share(exposure: float) -> float:
        return 100.0 * exposure / nav

    groups = []
    for label, chosen in (("Long", [i for i in items if i["exposure"] > 0]),
                          ("Short", [i for i in items if i["exposure"] < 0])):
        if chosen:
            groups.append({
                "label": label,
                "share": share(sum(abs(i["exposure"]) for i in chosen)),
                "items": [{**i, "share": share(abs(i["exposure"])),
                           "detail": f"{share(abs(i['exposure'])):.1f}% of NAV · "
                                     f"{KIND_LABELS.get(i['kind'], i['kind'])} · {i['currency']}"}
                          for i in chosen],
            })
    groups.append({
        "label": "Cash",
        "share": share(cash),
        "items": [{"name": "Constant cash, no interest", "kind": "cash", "currency": base_currency,
                   "exposure": cash, "share": share(cash),
                   "detail": f"{share(cash):.1f}% of NAV · {base_currency}"
                             + (" · borrowed" if cash < 0 else "")}],
    })
    return {
        "title": title,
        "subtitle": f"Fixed-unit composite · {base_currency} · starts at {nav:g}",
        "groups": groups,
        "totals": {"nav": nav, "long": share(long), "short": share(short), "gross": share(long + short),
                   "net": share(long - short), "cash": share(cash)},
    }


def filtered_tree(*, title: str, base_currency: str, source_name: str, excluded_countries: Sequence[str],
                  excluded_exchanges: Sequence[str], excluded_currencies: Sequence[str],
                  cash_currencies: Sequence[str], opening_cash: Mapping[str, float]) -> dict[str, Any]:
    """Holdings and cash groups for a filtered account."""

    excluded = [", ".join(values) for values in (excluded_countries, excluded_exchanges, excluded_currencies)
                if values]
    opening = ", ".join(f"{code} {float(amount):,.0f}" for code, amount in sorted(opening_cash.items()))
    return {
        "title": title,
        "subtitle": f"Filtered account · {base_currency}",
        "groups": [
            {"label": "Holdings", "share": None, "items": [{
                "name": source_name, "kind": "account", "currency": base_currency,
                "detail": "Excluding " + "; ".join(excluded) if excluded else "No exclusions"}]},
            {"label": "Cash", "share": None, "items": [{
                "name": "Rebuilt cash ledger", "kind": "cash", "currency": ", ".join(cash_currencies),
                "detail": f"{', '.join(cash_currencies)} · opening {opening or 'nil'}"}]},
        ],
        "totals": None,
    }


def tree_for_definition(definition: Mapping[str, Any], *, source_names: Mapping[str, str] | None = None
                        ) -> dict[str, Any]:
    """The tree for a stored filtered or composite definition."""

    title = str(definition.get("display_name") or definition.get("subject_id") or "")
    base = str(definition.get("base_currency") or "")
    if definition.get("subject_type") == "composite_portfolio":
        return composite_tree(title=title, base_currency=base, constant_cash=definition.get("constant_cash", 0),
                              sleeves=list(definition.get("components") or []))
    if definition.get("subject_type") == "filtered_portfolio":
        position_filter = dict(definition.get("position_filter") or {})
        cash_policy = dict(definition.get("cash_policy") or {})
        source = str(definition.get("source_account_id") or "")
        return filtered_tree(
            title=title, base_currency=base, source_name=(source_names or {}).get(source, source),
            excluded_countries=list(position_filter.get("excluded_countries") or []),
            excluded_exchanges=list(position_filter.get("excluded_exchanges") or []),
            excluded_currencies=list(position_filter.get("excluded_currencies") or []),
            cash_currencies=list(cash_policy.get("allowed_currencies") or []),
            opening_cash=dict(cash_policy.get("opening_cash") or {}))
    raise ValueError("Only filtered portfolios and fixed-unit composites have a composition tree")


# Drawing -------------------------------------------------------------------

_WIDTH = 760
_ROOT = (16, 220)        # x, width
_GROUP = (268, 150)
_LEAF = (466, 278)
_LEAF_HEIGHT = 62
_GAP = 12
_PAD = 16
_COLOURS = {"Long": "#0f7a4a", "Short": "#b42318", "Cash": "#667085", "Holdings": "#0057d9"}
_FONT = "Inter, 'Segoe UI', Arial, sans-serif"


def _lines(text: str, width: int, limit: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    if len(lines) > limit:
        lines = lines[:limit]
        lines[-1] = lines[-1][: max(width - 1, 1)].rstrip() + "…"
    return [line if len(line) <= width else line[: width - 1] + "…" for line in lines] or [""]


def _text(x: float, y: float, value: str, *, size: int = 13, weight: int = 400, fill: str = "#0b1220",
          anchor: str = "start") -> str:
    return (f'<text x="{x:.0f}" y="{y:.0f}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}">{escape(value)}</text>')


def tree_svg(tree: Mapping[str, Any]) -> str:
    """A self-contained SVG of the tree, readable on light and dark pages and in reports."""

    leaves = [(group, item) for group in tree["groups"] for item in group["items"]]
    height = _PAD * 2 + len(leaves) * _LEAF_HEIGHT + (len(leaves) - 1) * _GAP
    height = max(height, 150)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_WIDTH} {height}" width="100%" '
        f'role="img" aria-label="{escape("Composition of " + str(tree["title"]))}" '
        f'font-family="{_FONT}">',
        f'<rect x="0" y="0" width="{_WIDTH}" height="{height}" rx="18" fill="#ffffff" stroke="#e4e7ec"/>',
    ]
    root_mid = height / 2
    title_lines = _lines(str(tree["title"]), 24, 2)
    subtitle_lines = _lines(str(tree.get("subtitle") or ""), 32, 2)
    root_height = 26 + 18 * len(title_lines) + 15 * len(subtitle_lines)
    root_top = root_mid - root_height / 2
    x, width = _ROOT
    parts.append(f'<rect x="{x}" y="{root_top:.0f}" width="{width}" height="{root_height:.0f}" rx="12" '
                 f'fill="#eef4ff" stroke="#0057d9"/>')
    for index, line in enumerate(title_lines):
        parts.append(_text(x + 14, root_top + 24 + 18 * index, line, size=15, weight=650))
    for index, line in enumerate(subtitle_lines):
        parts.append(_text(x + 14, root_top + 22 + 18 * len(title_lines) + 15 * index, line, size=11,
                           fill="#475467"))

    top = _PAD
    for group in tree["groups"]:
        count = len(group["items"])
        span = count * _LEAF_HEIGHT + (count - 1) * _GAP
        middle = top + span / 2
        colour = _COLOURS.get(group["label"], "#475467")
        gx, gw = _GROUP
        parts.append(f'<path d="M{_ROOT[0] + _ROOT[1]} {root_mid:.0f} H{gx - 22} V{middle:.0f} H{gx}" '
                     f'fill="none" stroke="#98a2b3" stroke-width="1.5"/>')
        parts.append(f'<rect x="{gx}" y="{middle - 24:.0f}" width="{gw}" height="48" rx="10" fill="#ffffff" '
                     f'stroke="{colour}" stroke-width="1.5"/>')
        has_share = group.get("share") is not None
        parts.append(_text(gx + 12, middle - 3 if has_share else middle + 5, group["label"], size=14, weight=650,
                           fill=colour))
        if has_share:
            parts.append(_text(gx + 12, middle + 15, f"{group['share']:.1f}% of NAV", size=11, fill="#475467"))
        for index, item in enumerate(group["items"]):
            leaf_top = top + index * (_LEAF_HEIGHT + _GAP)
            leaf_mid = leaf_top + _LEAF_HEIGHT / 2
            lx, lw = _LEAF
            parts.append(f'<path d="M{gx + gw} {middle:.0f} H{lx - 20} V{leaf_mid:.0f} H{lx}" fill="none" '
                         f'stroke="#98a2b3" stroke-width="1.5"/>')
            parts.append(f'<rect x="{lx}" y="{leaf_top:.0f}" width="{lw}" height="{_LEAF_HEIGHT}" rx="10" '
                         f'fill="#f9fafb" stroke="#d0d5dd"/>')
            parts.append(f'<rect x="{lx}" y="{leaf_top:.0f}" width="5" height="{_LEAF_HEIGHT}" rx="2" '
                         f'fill="{colour}"/>')
            name_lines = _lines(str(item["name"]), 36, 2)
            detail_lines = _lines(str(item["detail"]), 44, 3 - len(name_lines))
            for line_index, line in enumerate(name_lines):
                parts.append(_text(lx + 16, leaf_top + 20 + 16 * line_index, line, size=13, weight=600))
            for line_index, line in enumerate(detail_lines):
                parts.append(_text(lx + 16, leaf_top + 20 + 16 * (len(name_lines) + line_index), line, size=11,
                                   fill="#475467"))
        top += span + _GAP
    parts.append("</svg>")
    return "".join(parts)
