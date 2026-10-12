"""In-memory FX evidence-key transition rehearsal; no storage or DB writes."""
import hashlib
import json
from collections import Counter

from .fx_source_evidence import rate_status
from .parser import _source_key, parse_flex_xml
from .snapshot_preflight import inspect_archived_xml
from .util import canonical_json, decimal_text, pick, sha256_text

FX_TYPES = {"conversionrate", "fxrate", "currencyconversionrate"}


def reused_archive_runs(*, archive_rows, source_runs, required_runs, tenant_id):
    """Link repeat ingestions by exact recorded content digest, never by date."""
    available = {row["content_sha256"] for row in archive_rows if row["tenant_id"] == tenant_id}
    registered = {row["run_id"]: row["content_sha256"] for row in archive_rows if row["tenant_id"] == tenant_id}
    indexed = {row["run_id"]: row for row in source_runs}
    if len(indexed) != len(source_runs):
        raise ValueError("Ambiguous ingestion lineage")
    links = {}
    for run_id in required_runs:
        row = indexed.get(run_id)
        if (not row or row["tenant_id"] != tenant_id or row["status"] != "success"
                or not row["completed_at"] or row["raw_sha256"] not in available):
            raise ValueError("Unverified repeated-ingestion lineage")
        if run_id in registered:
            if registered[run_id] != row["raw_sha256"]:
                raise ValueError("Ingestion and archive digests disagree")
        else:
            links.setdefault(row["raw_sha256"], []).append(run_id)
    return {sha: sorted(runs) for sha, runs in links.items()}


def observation(account, day, attrs, row_hash, run):
    return (run, account, day, pick(attrs, "fromCurrency", "currency"),
            pick(attrs, "toCurrency", "baseCurrency", default="BASE"),
            decimal_text(pick(attrs, "rate", "fxRateToBase", "exchangeRate")), row_hash)


def signature(run, account, section, kind, key, row_hash, day):
    return (run, account, section, kind, key, row_hash, day)


class EvidenceRehearsal:
    """Retain content versions and source-run lineage; never pick latest by guess."""
    def __init__(self, *, accounts, normalized, legacy):
        self.accounts = frozenset(accounts)
        if not self.accounts:
            raise ValueError("Account scope required")
        self.expected = set()
        self.invalid = set()
        natural = set()
        for row in normalized:
            if row["account_id"] not in self.accounts:
                raise ValueError("Normalized account scope mismatch")
            identity = (row["account_id"], row["rate_date"], row["from_currency"], row["to_currency"])
            if identity in natural:
                raise ValueError("Duplicate normalized FX identity")
            natural.add(identity)
            item = (row["last_seen_run_id"], *identity,
                    decimal_text(row["rate_text"]), row["row_sha256"])
            self.expected.add(item)
            if rate_status(row["rate_text"]) != "positive":
                self.invalid.add(item)
        self.aliases = {}
        for row in legacy:
            attrs = json.loads(row["data_json"])
            if (row["account_id"] not in self.accounts or row["record_type"].lower() not in FX_TYPES
                    or not isinstance(attrs, dict) or sha256_text(canonical_json(attrs)) != row["row_sha256"]):
                raise ValueError("Legacy evidence scope or content hash mismatch")
            key = _source_key(row["account_id"], row["section"], row["record_type"], attrs,
                              effective_report_date=row["report_date"])
            if not key.startswith("fx-rate:v2:"):
                raise ValueError("Unresolved legacy evidence identity")
            old = (row["account_id"], row["section"], row["record_type"], row["source_key"])
            if old in self.aliases:
                raise ValueError("Duplicate legacy evidence identity")
            self.aliases[old] = signature(row["last_seen_run_id"], row["account_id"], row["section"],
                row["record_type"], key, row["row_sha256"], row["report_date"])
        self.archives = set()
        self.versions = set()
        self.lineage = set()
        self.matched = set()
        self.statements = Counter()
        self.transfer_occurrences = 0

    def add_archive(self, content, *, expected_sha256, run_id):
        # Verify even repeated submissions; a caller cannot substitute bytes.
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ValueError("Archive content hash mismatch")
        receipt = (run_id, expected_sha256)
        if receipt in self.archives:
            return False
        if any(run == run_id for run, _ in self.archives):
            raise ValueError("Ambiguous multiple archives for source run")
        checked = inspect_archived_xml(content, expected_sha256=expected_sha256, accounts=set(self.accounts))
        report = parse_flex_xml(content)
        seen = {}
        lineage = set()
        observed = set()
        for row in report.rows:
            if row.account_id not in self.accounts or row.record_type.lower() not in FX_TYPES:
                continue
            if not row.source_key.startswith("fx-rate:v2:"):
                raise ValueError("Unresolved archived FX identity")
            key = (row.account_id, row.section, row.record_type, row.source_key)
            if key in seen and seen[key] != row.row_sha256:
                raise ValueError("Conflicting FX values within one archive")
            seen[key] = row.row_sha256
            lineage.add(signature(run_id, *key, row.row_sha256, row.report_date))
            observed.add(observation(row.account_id, row.report_date, row.attributes, row.row_sha256, run_id))
        # All validation precedes mutation. A rejected file leaves no partial data.
        self.archives.add(receipt)
        self.versions.update((*key, value) for key, value in seen.items())
        self.lineage.update(lineage)
        self.matched.update(self.expected & observed)
        for statement in checked["statements"]:
            self.statements[(statement["account_id"], statement["transfers_section"])] += 1
            self.transfer_occurrences += statement["transfer_records"]
        return True

    def summary(self):
        verified_aliases = sum(item in self.lineage for item in self.aliases.values())
        keys = {version[:-1] for version in self.versions}
        return {"archives_verified": len({sha for _, sha in self.archives}),
            "source_run_archive_links_verified": len(self.archives), "normalized_fx_expected": len(self.expected),
            "normalized_fx_verified": len(self.matched), "normalized_fx_unmatched": len(self.expected-self.matched),
            "invalid_fx_expected": len(self.invalid), "invalid_fx_verified": len(self.invalid & self.matched),
            "legacy_aliases_expected": len(self.aliases), "legacy_aliases_verified": verified_aliases,
            "legacy_aliases_unmatched": len(self.aliases)-verified_aliases,
            "canonical_keys": len(keys), "content_versions": len(self.versions),
            "source_lineage_records": len(self.lineage),
            "version_manifest_sha256": sha256_text(canonical_json(sorted(self.versions))),
            "lineage_manifest_sha256": sha256_text(canonical_json(sorted(self.lineage))),
            "transfers_statement_counts": [{"account_id": a, "section_status": s, "count": n}
                for (a, s), n in sorted(self.statements.items())],
            "transfer_record_occurrences": self.transfer_occurrences,
            "source_completeness": "unverified", "rehearsal_only": True,
            "financial_values_changed": False, "deployment_authorized": False}
