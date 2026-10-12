"""Bounded ARM collection reads. Never accept a truncated inventory as complete.

The injected transport receives relative paths only; continuation links cannot
redirect an ARM credential to another host, tenant scope or collection.
"""
from urllib.parse import parse_qsl, unquote, urlsplit
import re


class InventoryError(ValueError):
    """Inventory is incomplete or its continuation/identity is unsafe."""


def read_collection(request, path, *, max_pages=100, max_items=10000):
    """Return all resources or raise, with no partially successful result."""
    if type(max_pages) is not int or not 1 <= max_pages <= 1000:
        raise InventoryError("Invalid inventory page bound")
    if type(max_items) is not int or not 1 <= max_items <= 100000:
        raise InventoryError("Invalid inventory item bound")
    if not isinstance(path, str) or any(ord(c) < 33 or ord(c) > 126 for c in path):
        raise InventoryError("Invalid initial ARM path")
    initial = urlsplit(path)
    match = re.fullmatch(
        r"(/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[A-Za-z0-9_.()-]+)/"
        r"(resources|providers/[A-Za-z0-9.]+/[A-Za-z0-9]+)", initial.path,
        re.IGNORECASE,
    )
    if initial.scheme or initial.netloc or initial.fragment or not match:
        raise InventoryError("Expected resource-group ARM collection path")
    scope, collection = match.groups()
    params = parse_qsl(initial.query, keep_blank_values=True, strict_parsing=True)
    if len(params) != 1 or params[0][0] != "api-version" or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}(?:-preview)?", params[0][1]
    ):
        raise InventoryError("Expected one fixed ARM API version")
    version = params[0][1]

    def continuation(link):
        if not isinstance(link, str) or len(link) > 32768 or any(
            ord(c) < 33 or ord(c) > 126 for c in link
        ) or "\\" in link:
            raise InventoryError("Invalid ARM continuation")
        parsed = urlsplit(link)
        if parsed.fragment or (parsed.scheme or parsed.netloc) and (
            parsed.scheme != "https" or parsed.netloc.lower() != "management.azure.com"
        ) or parsed.path.casefold() != initial.path.casefold():
            raise InventoryError("ARM continuation escaped collection scope")
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        if len(query) != 2 or len({k.casefold() for k, _ in query}) != 2:
            raise InventoryError("Invalid ARM continuation parameters")
        pairs = {k.casefold(): v for k, v in query}
        if pairs.get("api-version") != version or not pairs.get("$skiptoken"):
            raise InventoryError("ARM continuation version/cursor mismatch")
        return parsed.path + "?" + parsed.query, pairs["$skiptoken"]

    rows, identities, links, cursors = [], set(), {path}, set()
    current = path
    for page_number in range(max_pages):
        page = request("GET", current)
        if type(page) is not dict or type(page.get("value")) is not list:
            raise InventoryError("Invalid ARM inventory page")
        for row in page["value"]:
            if type(row) is not dict or not isinstance(row.get("id"), str):
                raise InventoryError("Invalid ARM resource identity")
            identity = row["id"]
            prefix = scope + "/providers/" if collection.lower() == "resources" else initial.path + "/"
            if (not identity.casefold().startswith(prefix.casefold())
                    or identity == prefix or unquote(identity) != identity
                    or any(c in identity for c in ("?", "#", "\\"))
                    or any(ord(c) < 33 for c in identity)
                    or any(p in (".", "..", "") for p in identity.split("/")[1:])):
                raise InventoryError("ARM resource escaped collection scope")
            key = identity.casefold()
            if key in identities:
                raise InventoryError("Duplicate ARM resource identity")
            identities.add(key)
            rows.append(row)
            if len(rows) > max_items:
                raise InventoryError("ARM inventory item bound exceeded")
        link = page.get("nextLink")
        if link is None or link == "":
            return rows
        current, cursor = continuation(link)
        if current in links or cursor in cursors:
            raise InventoryError("ARM inventory continuation loop")
        links.add(current)
        cursors.add(cursor)
    raise InventoryError("ARM inventory page bound exceeded")
