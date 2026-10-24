"""Medallion tags on the resources whose own code compares their whole tag map.

The resource group's tag rule refuses a resource that is created or changed without layer,
component and owner (docs/MEDALLION_ARCHITECTURE.md, "Tag rule"). The code that made these
resources checks every tag exactly, so step S4a left them untagged and the rule excluded them by
ID on 24 Oct 2026. Their code now accepts each one with exactly these extra tags, or with none of
them (as before the tag line runs), and the code that redeploys them writes these tags itself.
They already carry an owner tag.
"""
from __future__ import annotations

# Live name (the workbook by its purpose, since its name is a GUID) -> the tags added.
EXACT: dict[str, dict[str, str]] = {
    "ibkrv2stg-metadata-monitor": {"layer": "ops", "component": "monitoring", "hop": "ops"},
    "ibkrv2stg-secret-metadata": {"layer": "ops", "component": "monitoring", "hop": "ops"},
    "ibkrv2stg-secret-metadata-identity": {"layer": "foundation", "component": "identity"},
    "ibkr-v2-operational-data-findings": {"layer": "ops", "component": "alerts"},
    "ibkr-v2-secret-metadata-findings": {"layer": "ops", "component": "alerts"},
    "resource-cost-visibility": {"layer": "ops", "component": "monitoring"},
}


def with_medallion(name: str, tags: dict) -> dict:
    """The tags a deploy of this resource writes: its own tags plus its Medallion tags."""
    return {**tags, **EXACT[name]}


def without_medallion(name: str, tags: dict) -> dict | None:
    """The tags with this resource's Medallion tags taken out, or None when they are only partly
    there or carry other values."""
    medallion = EXACT[name]
    present = {key: tags[key] for key in medallion if key in tags}
    if present and present != medallion:
        return None
    return {key: value for key, value in tags.items() if key not in medallion}


def same_tags(name: str, actual, expected: dict) -> bool:
    """True when actual is expected, with or without this resource's Medallion tags."""
    if not isinstance(actual, dict):
        return False
    rest = without_medallion(name, actual)
    return rest is not None and rest == without_medallion(name, expected)
