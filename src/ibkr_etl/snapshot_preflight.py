"""Read-only import compatibility and hash-bound archived XML diagnostics.

No import, completeness certification, workflow authority or value correction.
"""
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter
from urllib.parse import urlsplit, unquote

from .snapshot_atomic_import import TABLE_KEYS, validate_combined
from .snapshot_evidence import FX_TYPES
from .snapshot_evidence_import import KEY_COLUMNS, PAYLOAD_COLUMNS
from .util import canonical_json, decimal_text, parse_date_text, pick, sha256_text


def compare_records(source, destination, *, keys, columns, json_columns=()):
    def inventory(rows):
        result = {}
        for row in rows:
            identity = tuple(row[k] for k in keys)
            if None in identity or identity in result:
                raise ValueError("Ambiguous preflight record identity")
            result[identity] = row
        return result
    before, after = inventory(source), inventory(destination)
    conflicts = Counter()
    equal = missing = different = 0
    for key, row in before.items():
        if key not in after:
            missing += 1
            continue
        changed = []
        for column in columns:
            left, right = row[column], after[key].get(column)
            if column in json_columns:
                try:
                    left, right = json.loads(left), json.loads(right)
                except (ValueError, TypeError):
                    changed.append(column)
                    continue
            if left != right:
                changed.append(column)
        if changed:
            different += 1
            conflicts.update(changed)
        else:
            equal += 1
    return {"source_rows": len(before), "matching_rows": equal, "missing_rows": missing,
            "conflicting_rows": different, "conflicting_columns": dict(conflicts),
            "destination_extra_rows": len(set(after) - set(before))}


def preflight_snapshot(connection, bundle, *, tenant_id):
    from psycopg import sql
    from psycopg.rows import dict_row
    validate_combined(bundle)
    account = bundle["evidence"]["account_id"]
    with connection.cursor(row_factory=dict_row) as c:
        if c.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] != "on":
            raise ValueError("Preflight requires database-enforced read-only mode")
        isolation = c.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
        if isolation not in ("repeatable read", "serializable"):
            raise ValueError("Preflight requires a consistent database snapshot")
        context = c.execute("SELECT current_setting('app.tenant_id',true) AS tenant").fetchone()["tenant"]
        if context != tenant_id:
            raise ValueError("Preflight tenant mismatch")
        owner = c.execute("SELECT base_currency FROM public.accounts WHERE tenant_id=%s AND account_id=%s",
                          (tenant_id, account)).fetchone()
        if not owner:
            raise ValueError("Preflight account is not accessible in this tenant")
        summaries = {}
        run_ids = set()
        for table, keys in TABLE_KEYS.items():
            data = bundle["tables"][table]
            target_columns = {r["column_name"] for r in c.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (table,)
            )}
            if set(data["columns"]) != target_columns - {"tenant_id", "last_seen_run_id"}:
                summaries[table] = {"column_contract_matches": False}
                continue
            if table == "instruments":
                rows = c.execute("SELECT * FROM public.instruments WHERE conid=ANY(%s)",
                                 ([r["conid"] for r in data["rows"]],)).fetchall()
                columns = [k for k in data["columns"] if k not in {"first_seen_at", "last_seen_at"}]
            else:
                rows = c.execute(sql.SQL("SELECT t.* FROM {} t JOIN public.accounts a ON a.account_id=t.account_id WHERE a.tenant_id=%s AND t.account_id=%s")
                                 .format(sql.Identifier("public", table)), (tenant_id, account)).fetchall()
                columns = data["columns"]
                source_keys = {tuple(row[k] for k in keys) for row in data["rows"]}
                run_ids.update(row["last_seen_run_id"] for row in rows if tuple(row[k] for k in keys) in source_keys)
            summaries[table] = {"column_contract_matches": True,
                               **compare_records(data["rows"], rows, keys=keys, columns=columns)}
        evidence = c.execute("""SELECT * FROM public.flex_rows WHERE tenant_id=%s AND account_id=%s
            AND (section='Transfers' OR lower(record_type)=ANY(%s))""", (tenant_id, account, list(FX_TYPES))).fetchall()
        evidence_keys = {tuple(row[k] for k in KEY_COLUMNS) for row in bundle["evidence"]["rows"]}
        for row in evidence:
            if tuple(row[k] for k in KEY_COLUMNS) in evidence_keys:
                run_ids.update([row["first_seen_run_id"], row["last_seen_run_id"]])
        summaries["financial_evidence"] = compare_records(bundle["evidence"]["rows"], evidence,
            keys=KEY_COLUMNS, columns=PAYLOAD_COLUMNS, json_columns=("data_json",))
        verified_runs = {row["run_id"] for row in c.execute(
            "SELECT run_id FROM public.ingestion_runs WHERE tenant_id=%s AND run_id=ANY(%s) AND status='success'",
            (tenant_id, sorted(run_ids)),
        )}
        currency_matches = owner["base_currency"] == bundle["account_base_currency"]
        compatible = currency_matches and run_ids == verified_runs and all(
            result.get("column_contract_matches", True) and result.get("conflicting_rows", 0) == 0
            for result in summaries.values()
        )
        return {"account_id": account, "snapshot_sha256": bundle["evidence"]["snapshot_sha256"],
                "value_transfer_compatible": compatible, "currency_matches": currency_matches,
                "unverified_destination_runs": len(run_ids - verified_runs), "tables": summaries,
                "source_completeness": "unverified", "execution_authorized": False,
                "database_read_only": True}


def archive_blob_location(url: str, *, expected_host: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != expected_host or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Unapproved raw archive location")
    parts = unquote(parsed.path).strip("/").split("/", 1)
    if len(parts) != 2 or parts[0] != "raw-statements" or any(p in ("", ".", "..") for p in parts[1].split("/")):
        raise ValueError("Unapproved raw archive scope")
    return parts[0], parts[1]


class _NoDoctype(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError("DTD declarations are not accepted in archived statements")


def inspect_archived_xml(content: bytes, *, expected_sha256: str, accounts: set[str], invalid_identities=None) -> dict:
    if len(content) > 64 * 1024 * 1024:
        raise ValueError("Archived XML exceeds diagnostic size limit")
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError("Archived XML hash mismatch")
    root = ET.fromstring(content, parser=ET.XMLParser(target=_NoDoctype()))
    name = lambda e: e.tag.rsplit("}", 1)[-1]
    invalid_identities = invalid_identities or set()
    statements = []
    matched_invalid = set()
    values_match = set()
    for statement in root.iter():
        if name(statement) != "FlexStatement":
            continue
        account = pick(statement.attrib, "accountId", "account")
        if account not in accounts:
            continue
        children = list(statement)
        transfers = [e for e in children if name(e) == "Transfers"]
        transfer_count = sum(1 for section in transfers for e in section.iter()
                             if e is not section and e.attrib and not list(e))
        counts = Counter()
        for element in statement.iter():
            if name(element).lower() not in FX_TYPES:
                continue
            attrs = dict(element.attrib)
            row_account = pick(attrs, "accountId", "account", default=account)
            if row_account in ("-", "UNKNOWN"):
                row_account = account
            if row_account != account:
                continue
            counts["fx_rows"] += 1
            day = parse_date_text(pick(attrs, "reportDate", "tradeDate", "date", "effectiveDate", "toDate")) or parse_date_text(pick(statement.attrib, "toDate"))
            identity = (account, day, sha256_text(canonical_json(attrs)))
            if identity in invalid_identities:
                matched_invalid.add(identity)
                if isinstance(invalid_identities, dict) and decimal_text(pick(attrs, "rate", "fxRateToBase", "exchangeRate")) == invalid_identities[identity]:
                    values_match.add(identity)
        statements.append({"account_id": account, "from_date": parse_date_text(pick(statement.attrib, "fromDate")),
                           "to_date": parse_date_text(pick(statement.attrib, "toDate")),
                           "transfers_section": "missing" if not transfers else ("present_nonempty" if transfer_count else "present_empty"),
                           "transfer_records": transfer_count, "fx_rows": counts["fx_rows"]})
    return {"archive_sha256_verified": True, "statements": statements,
            "matched_invalid_row_hashes": len(matched_invalid),
            "matched_invalid_source_values": len(values_match),
            "matching_invalid_by_account": dict(Counter(item[0] for item in matched_invalid)),
            "source_completeness": "unverified", "sample_only": True}
