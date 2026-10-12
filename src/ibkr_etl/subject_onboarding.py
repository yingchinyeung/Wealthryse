from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping, Sequence


SUBJECT_TYPES = (
    "physical_account",
    "master_account",
    "filtered_portfolio",
    "composite_portfolio",
)
DERIVED_SUBJECT_TYPES = ("filtered_portfolio", "composite_portfolio")
MASTER_REPORTING_MODES = (
    "administrative_only",
    "consolidated_children",
    "standalone_investable",
)
COMPONENT_TYPES = ("account", "portfolio", "benchmark", "cash")
DEPENDENCY_TYPES = (
    "source_account",
    "portfolio_component",
    "benchmark",
    "fx",
    "cash_ledger",
)

POST_WAVE0_ONBOARDING_SEQUENCE = (
    "China & Hong Kong",
    "Alpha World MS",
    "Alpha World T",
    "Master-account test",
    "Remaining v1.1 physical accounts",
)

REFERENCE_ONBOARDING_PROGRAMME = (
    {
        "subject_id": "hong-kong-china",
        "display_name": "China & Hong Kong",
        "subject_type": "filtered_portfolio",
        "predecessor_subject_id": "U16088606",
        "required_subject_ids": ("U16088606",),
        "source_system": "derived_definition",
    },
    {
        "subject_id": "alpha-world-ms",
        "display_name": "Alpha World MS",
        "subject_type": "composite_portfolio",
        "predecessor_subject_id": "hong-kong-china",
        "required_subject_ids": (
            "hong-kong-china",
            "U16088571",
        ),
        "source_system": "derived_definition",
    },
    {
        "subject_id": "alpha-world-t",
        "display_name": "Alpha World T",
        "subject_type": "composite_portfolio",
        "predecessor_subject_id": "alpha-world-ms",
        "required_subject_ids": (
            "alpha-world-ms",
            "U16088606",
            "U16088571",
        ),
        "source_system": "derived_definition",
    },
    {
        "subject_id": "U15606638",
        "display_name": "U15606638 Master Account",
        "subject_type": "master_account",
        "predecessor_subject_id": "alpha-world-t",
        "required_subject_ids": ("alpha-world-t",),
        "source_system": "master_definition",
    },
)

_ACCOUNT_ID = re.compile(r"^[A-Z][A-Z0-9-]{2,31}$")
_SUBJECT_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{2,63}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class Dependency:
    subject_id: str
    depends_on_subject_id: str
    dependency_type: str
    effective_from: str
    configuration: Mapping[str, Any]


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def configuration_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _iso_date(value: str, *, field: str) -> str:
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO date") from error


def _subject_id(value: str, *, field: str = "subject_id") -> str:
    candidate = str(value or "").strip()
    if not _SUBJECT_ID.fullmatch(candidate):
        raise ValueError(f"{field} contains unsupported characters")
    return candidate


def _account_id(value: str, *, field: str = "account_id") -> str:
    candidate = str(value or "").strip().upper()
    if not _ACCOUNT_ID.fullmatch(candidate):
        raise ValueError(f"{field} is not a valid broker account identifier")
    return candidate


def _currency(value: str, *, field: str) -> str:
    candidate = str(value or "").strip().upper()
    if not _CURRENCY.fullmatch(candidate):
        raise ValueError(f"{field} must be a three-letter currency")
    return candidate


def physical_account_definition(
    *,
    account_id: str,
    display_name: str,
    base_currency: str,
    reporting_currency: str,
    history_start_date: str,
    flex_source_id: str,
) -> dict[str, Any]:
    """Return a validated, serializable physical-account onboarding contract."""

    account = _account_id(account_id)
    source = str(flex_source_id or "").strip()
    if not source:
        raise ValueError("flex_source_id is required for a physical account")
    return {
        "definition_version": 1,
        "subject_type": "physical_account",
        "subject_id": account,
        "display_name": str(display_name or account).strip(),
        "base_currency": _currency(base_currency, field="base_currency"),
        "reporting_currency": _currency(
            reporting_currency, field="reporting_currency"
        ),
        "history_start_date": _iso_date(
            history_start_date, field="history_start_date"
        ),
        "source": {
            "kind": "ibkr_flex_account",
            "flex_source_id": source,
            "source_account_id": account,
        },
    }


def master_account_definition(
    *,
    master_account_id: str,
    display_name: str,
    reporting_mode: str,
    reporting_currency: str,
    history_start_date: str,
    member_account_ids: Sequence[str],
) -> dict[str, Any]:
    """Return a master-account contract that cannot silently double count children."""

    master = _account_id(master_account_id, field="master_account_id")
    mode = str(reporting_mode or "").strip()
    if mode not in MASTER_REPORTING_MODES:
        raise ValueError("reporting_mode is not supported")
    members = sorted({_account_id(value, field="member_account_id") for value in member_account_ids})
    if master in members:
        raise ValueError("A master account cannot be its own child")
    if mode == "consolidated_children" and not members:
        raise ValueError("A consolidated master account requires at least one child")
    if mode != "consolidated_children" and members:
        raise ValueError("Child membership is only allowed for consolidated reporting")
    return {
        "definition_version": 1,
        "subject_type": "master_account",
        "subject_id": master,
        "display_name": str(display_name or master).strip(),
        "reporting_currency": _currency(
            reporting_currency, field="reporting_currency"
        ),
        "history_start_date": _iso_date(
            history_start_date, field="history_start_date"
        ),
        "reporting_mode": mode,
        "member_account_ids": members,
        "aggregation": {
            "count_master_totals": mode == "standalone_investable",
            "count_child_totals": mode == "consolidated_children",
            "eliminate_internal_transfers": mode == "consolidated_children",
        },
    }


def filtered_portfolio_definition(
    *,
    subject_id: str,
    display_name: str,
    source_account_id: str,
    base_currency: str,
    history_start_date: str,
    excluded_countries: Sequence[str],
    excluded_exchanges: Sequence[str],
    excluded_currencies: Sequence[str],
    opening_cash: Mapping[str, float],
    allowed_cash_currencies: Sequence[str],
    include_actual_interest: bool,
) -> dict[str, Any]:
    """Build the governed filtered-account definition used by China & Hong Kong."""

    portfolio = _subject_id(subject_id)
    source = _account_id(source_account_id, field="source_account_id")
    allowed = sorted(
        {_currency(value, field="allowed_cash_currency") for value in allowed_cash_currencies}
    )
    if not allowed:
        raise ValueError("At least one allowed cash currency is required")
    balances: dict[str, float] = {}
    for currency, amount in opening_cash.items():
        code = _currency(currency, field="opening_cash currency")
        if code not in allowed:
            raise ValueError("Opening cash is present in a currency that is not allowed")
        number = float(amount)
        if not math.isfinite(number):
            raise ValueError("Opening cash must be finite")
        balances[code] = number
    for currency in allowed:
        balances.setdefault(currency, 0.0)
    excluded_ccy = sorted(
        {_currency(value, field="excluded_currency") for value in excluded_currencies}
    )
    countries = sorted(
        {str(value).strip() for value in excluded_countries if str(value).strip()}
    )
    exchanges = sorted(
        {str(value).strip().upper() for value in excluded_exchanges if str(value).strip()}
    )
    if not countries and not exchanges and not excluded_ccy:
        raise ValueError("A filtered portfolio requires at least one exclusion rule")
    if set(excluded_ccy).intersection(allowed):
        raise ValueError("An excluded currency cannot also be an allowed cash currency")
    return {
        "definition_version": 1,
        "subject_type": "filtered_portfolio",
        "calculation_kind": "filtered_account_with_reconstructed_cash",
        "subject_id": portfolio,
        "display_name": str(display_name).strip(),
        "base_currency": _currency(base_currency, field="base_currency"),
        "history_start_date": _iso_date(
            history_start_date, field="history_start_date"
        ),
        "source_account_id": source,
        "position_filter": {
            "excluded_countries": countries,
            "excluded_exchanges": exchanges,
            "excluded_currencies": excluded_ccy,
            "match_policy": "exclude_when_any_signal_matches",
        },
        "cash_policy": {
            "replace_source_cash": True,
            "opening_cash": balances,
            "allowed_currencies": allowed,
            "exclude_events_linked_to_excluded_positions": True,
            "exclude_fx_trade_when_any_leg_uses_excluded_currency": True,
            "include_actual_posted_interest": bool(include_actual_interest),
            "assumed_interest_rate": 0.0,
            "deduplicate_economic_events": True,
        },
        "publication_policy": {
            "require_same_date_source_nav_and_positions": True,
            "invent_missing_dates": False,
        },
    }


def fixed_unit_composite_definition(
    *,
    subject_id: str,
    display_name: str,
    base_currency: str,
    normalization_date: str,
    constant_cash: float,
    components: Sequence[Mapping[str, Any]],
    market_carry_days: int = 7,
) -> dict[str, Any]:
    """Build and validate a fixed-unit composite without accepting executable formulas."""

    portfolio = _subject_id(subject_id)
    if not 0 <= int(market_carry_days) <= 14:
        raise ValueError("market_carry_days must be between 0 and 14")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in components:
        component_id = _subject_id(str(raw.get("component_id") or ""), field="component_id")
        if component_id == portfolio:
            raise ValueError("A composite cannot contain itself")
        if component_id in seen:
            raise ValueError(f"Duplicate composite component: {component_id}")
        seen.add(component_id)
        component_type = str(raw.get("component_type") or "").strip()
        if component_type not in COMPONENT_TYPES or component_type == "cash":
            raise ValueError("Composite sleeves must be account, portfolio or benchmark")
        coefficient = float(raw.get("coefficient"))
        if not math.isfinite(coefficient) or coefficient == 0:
            raise ValueError("Composite component coefficients must be finite and non-zero")
        price_convention = str(raw.get("price_convention") or "nav").strip()
        if component_type == "benchmark" and price_convention not in {
            "price_index_close",
            "total_return_index_close",
            "unadjusted_official_close",
        }:
            raise ValueError("Benchmark components require an explicit price convention")
        normalized.append(
            {
                "component_id": component_id,
                "component_name": str(raw.get("component_name") or component_id).strip(),
                "component_type": component_type,
                "coefficient": coefficient,
                "direction": "long" if coefficient > 0 else "short",
                "source_currency": _currency(
                    str(raw.get("source_currency") or ""), field="source_currency"
                ),
                "price_convention": price_convention,
            }
        )
    if not normalized:
        raise ValueError("A composite requires at least one non-cash component")
    if not any(item["component_type"] in {"account", "portfolio"} for item in normalized):
        raise ValueError(
            "A composite requires an account or portfolio sleeve to define its daily calendar"
        )
    cash = float(constant_cash)
    if not math.isfinite(cash):
        raise ValueError("Constant cash must be finite")
    initial_nav = cash + 100.0 * sum(item["coefficient"] for item in normalized)
    if initial_nav <= 0:
        raise ValueError("The composite must have positive NAV on the normalization date")
    long_exposure = 100.0 * sum(
        item["coefficient"] for item in normalized if item["coefficient"] > 0
    )
    short_exposure = -100.0 * sum(
        item["coefficient"] for item in normalized if item["coefficient"] < 0
    )
    return {
        "definition_version": 1,
        "subject_type": "composite_portfolio",
        "calculation_kind": "fixed_unit_normalized_composite",
        "subject_id": portfolio,
        "display_name": str(display_name).strip(),
        "base_currency": _currency(base_currency, field="base_currency"),
        "normalization_date": _iso_date(
            normalization_date, field="normalization_date"
        ),
        "normalization_base": 100.0,
        "constant_cash": cash,
        "cash_interest_rate": 0.0,
        "rebalancing_policy": "fixed_units_no_daily_rebalancing",
        "market_carry_days": int(market_carry_days),
        "components": normalized,
        "initial_exposure": {
            "nav": initial_nav,
            "long_risky": long_exposure,
            "short_risky": short_exposure,
            "gross_risky": long_exposure + short_exposure,
            "net_risky": long_exposure - short_exposure,
            "cash": cash,
        },
    }


def dependencies_for_definition(definition: Mapping[str, Any]) -> list[Dependency]:
    """Translate one governed definition into dependency-graph rows."""

    subject_id = _subject_id(str(definition.get("subject_id") or ""))
    effective_from = str(
        definition.get("history_start_date")
        or definition.get("normalization_date")
        or ""
    )
    _iso_date(effective_from, field="effective_from")
    kind = definition.get("subject_type")
    rows: list[Dependency] = []
    if kind == "filtered_portfolio":
        source = _account_id(
            str(definition.get("source_account_id") or ""), field="source_account_id"
        )
        rows.append(
            Dependency(subject_id, source, "source_account", effective_from, {})
        )
        rows.append(
            Dependency(subject_id, source, "cash_ledger", effective_from, {})
        )
    elif kind == "composite_portfolio":
        for component in definition.get("components", []):
            component_type = str(component["component_type"])
            dependency_type = (
                "benchmark" if component_type == "benchmark" else "portfolio_component"
            )
            rows.append(
                Dependency(
                    subject_id,
                    str(component["component_id"]),
                    dependency_type,
                    effective_from,
                    {"coefficient": component["coefficient"]},
                )
            )
    elif kind == "master_account" and definition.get("reporting_mode") == "consolidated_children":
        for member in definition.get("member_account_ids", []):
            rows.append(
                Dependency(subject_id, str(member), "portfolio_component", effective_from, {})
            )
    return rows


def topological_refresh_order(
    subject_ids: Iterable[str], dependencies: Iterable[Dependency]
) -> list[str]:
    """Return a deterministic dependency-first order, rejecting unknowns and cycles."""

    nodes = {_subject_id(value) for value in subject_ids}
    parents: dict[str, set[str]] = {value: set() for value in nodes}
    children: dict[str, set[str]] = {value: set() for value in nodes}
    for dependency in dependencies:
        child = _subject_id(dependency.subject_id)
        parent = _subject_id(dependency.depends_on_subject_id)
        if child not in nodes or parent not in nodes:
            raise ValueError("Every dependency endpoint must be a registered subject")
        if child == parent:
            raise ValueError("A subject cannot depend on itself")
        parents[child].add(parent)
        children[parent].add(child)
    ready = sorted(node for node, values in parents.items() if not values)
    ordered: list[str] = []
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        for child in sorted(children[node]):
            parents[child].discard(node)
            if not parents[child] and child not in ordered and child not in ready:
                ready.append(child)
        ready.sort()
    if len(ordered) != len(nodes):
        cycle_nodes = sorted(node for node, values in parents.items() if values)
        raise ValueError(f"Dependency cycle detected: {', '.join(cycle_nodes)}")
    return ordered


def approved_reference_definitions() -> dict[str, dict[str, Any]]:
    """Return the approved v1.1 derived definitions as migration references."""

    hk_china = filtered_portfolio_definition(
        subject_id="hong-kong-china",
        display_name="China & Hong Kong",
        source_account_id="U16088606",
        base_currency="USD",
        history_start_date="2025-01-20",
        excluded_countries=("Taiwan",),
        excluded_exchanges=("TWSE", "TWO", "TPEX", "ROCO", "TAIFEX"),
        excluded_currencies=("TWD",),
        opening_cash={"HKD": 484300.0, "USD": 0.0},
        allowed_cash_currencies=("HKD", "USD"),
        include_actual_interest=True,
    )
    common_components = (
        {
            "component_id": "U16088571",
            "component_name": "U16088571 (U.S. Technology and Biotechnology)",
            "component_type": "account",
            "coefficient": 0.45,
            "source_currency": "USD",
            "price_convention": "nav",
        },
        {
            "component_id": "hang-seng-price-index",
            "component_name": "Hang Seng Index price index",
            "component_type": "benchmark",
            "coefficient": -0.49,
            "source_currency": "HKD",
            "price_convention": "price_index_close",
        },
        {
            "component_id": "spy-settlement",
            "component_name": "SPY official close",
            "component_type": "benchmark",
            "coefficient": -0.45,
            "source_currency": "USD",
            "price_convention": "unadjusted_official_close",
        },
    )
    alpha_ms = fixed_unit_composite_definition(
        subject_id="alpha-world-ms",
        display_name="Alpha World MS",
        base_currency="HKD",
        normalization_date="2025-01-20",
        constant_cash=58.0,
        components=(
            {
                "component_id": "hong-kong-china",
                "component_name": "China & Hong Kong",
                "component_type": "portfolio",
                "coefficient": 0.91,
                "source_currency": "USD",
                "price_convention": "nav",
            },
            *common_components,
        ),
    )
    alpha_t = fixed_unit_composite_definition(
        subject_id="alpha-world-t",
        display_name="Alpha World T",
        base_currency="HKD",
        normalization_date="2025-01-20",
        constant_cash=58.0,
        components=(
            {
                "component_id": "U16088606",
                "component_name": "U16088606-Greater China",
                "component_type": "account",
                "coefficient": 0.91,
                "source_currency": "USD",
                "price_convention": "nav",
            },
            *common_components,
        ),
    )
    return {
        "hong-kong-china": hk_china,
        "alpha-world-ms": alpha_ms,
        "alpha-world-t": alpha_t,
    }


def validate_reference_runtime_contract(
    subject_id: str,
    configuration: Mapping[str, Any] | str,
    stored_sha256: str,
) -> dict[str, Any]:
    """Bind a scheduled calculation to the exact owner-approved reference contract.

    The named reference calculators are intentionally allow-listed.  Until the
    generic typed interpreter is released, they must never run against a database
    definition that differs from the reviewed server-owned definition, even when
    that database row has a syntactically valid hash of its own.
    """

    subject = _subject_id(subject_id)
    expected = approved_reference_definitions().get(subject)
    if expected is None:
        raise ValueError(f"No approved reference calculator exists for {subject}")
    if isinstance(configuration, str):
        try:
            parsed = json.loads(configuration)
        except json.JSONDecodeError as error:
            raise ValueError("Stored calculation definition is not valid JSON") from error
    else:
        parsed = dict(configuration)
    if not isinstance(parsed, dict):
        raise ValueError("Stored calculation definition must be a JSON object")
    actual_digest = configuration_sha256(parsed)
    if actual_digest != str(stored_sha256 or ""):
        raise ValueError("Stored calculation definition does not match its SHA-256")
    expected_digest = configuration_sha256(expected)
    if actual_digest != expected_digest or parsed != expected:
        raise ValueError(
            "Approved database definition differs from the deployed reference contract"
        )
    return parsed


def validate_typed_runtime_contract(
    subject_id: str,
    subject_type: str,
    configuration: Mapping[str, Any] | str,
    stored_sha256: str,
) -> dict[str, Any]:
    """Rebuild and verify any Studio definition without evaluating user code.

    Only the two allow-listed calculation schemas can pass.  Reconstructing the
    definition through the public builders removes unknown keys, reapplies every
    validation rule and proves that the stored hash represents canonical data.
    The three reference subjects receive the additional exact-contract check.
    """

    subject = _subject_id(subject_id)
    if isinstance(configuration, str):
        try:
            parsed = json.loads(configuration)
        except json.JSONDecodeError as error:
            raise ValueError("Stored calculation definition is not valid JSON") from error
    else:
        parsed = dict(configuration)
    if not isinstance(parsed, dict):
        raise ValueError("Stored calculation definition must be a JSON object")
    actual_digest = configuration_sha256(parsed)
    if actual_digest != str(stored_sha256 or ""):
        raise ValueError("Stored calculation definition does not match its SHA-256")
    if str(parsed.get("subject_id") or "") != subject:
        raise ValueError("Stored calculation definition belongs to another subject")
    kind = str(subject_type or parsed.get("subject_type") or "")
    if kind != str(parsed.get("subject_type") or ""):
        raise ValueError("Stored subject type differs from its calculation definition")
    if kind == "filtered_portfolio":
        position_filter = dict(parsed.get("position_filter") or {})
        cash_policy = dict(parsed.get("cash_policy") or {})
        rebuilt = filtered_portfolio_definition(
            subject_id=subject,
            display_name=str(parsed.get("display_name") or ""),
            source_account_id=str(parsed.get("source_account_id") or ""),
            base_currency=str(parsed.get("base_currency") or ""),
            history_start_date=str(parsed.get("history_start_date") or ""),
            excluded_countries=position_filter.get("excluded_countries") or (),
            excluded_exchanges=position_filter.get("excluded_exchanges") or (),
            excluded_currencies=position_filter.get("excluded_currencies") or (),
            opening_cash=cash_policy.get("opening_cash") or {},
            allowed_cash_currencies=cash_policy.get("allowed_currencies") or (),
            include_actual_interest=bool(
                cash_policy.get("include_actual_posted_interest")
            ),
        )
    elif kind == "composite_portfolio":
        rebuilt = fixed_unit_composite_definition(
            subject_id=subject,
            display_name=str(parsed.get("display_name") or ""),
            base_currency=str(parsed.get("base_currency") or ""),
            normalization_date=str(parsed.get("normalization_date") or ""),
            constant_cash=float(parsed.get("constant_cash")),
            components=parsed.get("components") or (),
            market_carry_days=int(parsed.get("market_carry_days", 7)),
        )
    else:
        raise ValueError("Only filtered portfolios and fixed-unit composites are supported")
    if rebuilt != parsed:
        raise ValueError("Stored calculation definition is not canonical")
    if subject in approved_reference_definitions():
        validate_reference_runtime_contract(subject, parsed, stored_sha256)
    return rebuilt


def derived_portfolio_studio_templates() -> list[dict[str, Any]]:
    """Describe the no-code templates available to the website wizard."""

    return [
        {
            "template_id": "filtered-account-reconstructed-cash-v1",
            "subject_type": "filtered_portfolio",
            "title": "Filtered account with reconstructed cash",
            "modelled_on": "China & Hong Kong",
            "supports": [
                "country_exchange_currency_exclusions",
                "multi_currency_opening_cash",
                "posted_cash_activity",
                "actual_interest_policy",
            ],
        },
        {
            "template_id": "fixed-unit-normalized-composite-v1",
            "subject_type": "composite_portfolio",
            "title": "Fixed-unit normalized composite",
            "modelled_on": "Alpha World MS and Alpha World T",
            "supports": [
                "long_short_account_and_portfolio_sleeves",
                "approved_market_benchmarks",
                "common_currency_conversion",
                "constant_non_interest_bearing_cash",
                "no_daily_rebalancing",
            ],
        },
    ]


def reference_definition_summary(definition: Mapping[str, Any]) -> dict[str, Any]:
    """Return a non-secret, client-readable composition preview for the website."""

    subject_type = str(definition.get("subject_type") or "")
    if subject_type == "filtered_portfolio":
        cash_policy = dict(definition.get("cash_policy") or {})
        position_filter = dict(definition.get("position_filter") or {})
        return {
            "calculation_kind": definition.get("calculation_kind"),
            "source": definition.get("source_account_id"),
            "effective_from": definition.get("history_start_date"),
            "position_policy": (
                "Keep non-Taiwan positions; exclude a holding when any approved "
                "country, exchange or currency signal identifies Taiwan."
            ),
            "excluded_countries": list(position_filter.get("excluded_countries") or []),
            "excluded_exchanges": list(position_filter.get("excluded_exchanges") or []),
            "excluded_currencies": list(position_filter.get("excluded_currencies") or []),
            "cash_policy": (
                "Replace source-account cash with the reconstructed HKD/USD ledger; "
                "include eligible posted activity and actual interest; apply no assumed interest."
            ),
            "opening_cash": dict(cash_policy.get("opening_cash") or {}),
            "allowed_cash_currencies": list(cash_policy.get("allowed_currencies") or []),
        }
    if subject_type == "composite_portfolio":
        components = [
            {
                "component_id": row.get("component_id"),
                "component_name": row.get("component_name"),
                "component_type": row.get("component_type"),
                "coefficient": row.get("coefficient"),
                "direction": row.get("direction"),
                "source_currency": row.get("source_currency"),
                "price_convention": row.get("price_convention"),
            }
            for row in definition.get("components", [])
        ]
        terms = [f"{float(definition.get('constant_cash') or 0):g}"]
        for row in components:
            coefficient = float(row["coefficient"])
            sign = "+" if coefficient >= 0 else "-"
            terms.append(
                f"{sign} {abs(coefficient):g} × normalized {row['component_name']}"
            )
        return {
            "calculation_kind": definition.get("calculation_kind"),
            "effective_from": definition.get("normalization_date"),
            "formula": " ".join(terms),
            "base_currency": definition.get("base_currency"),
            "normalization_base": definition.get("normalization_base"),
            "constant_cash": definition.get("constant_cash"),
            "cash_policy": "Constant non-interest-bearing cash",
            "rebalancing_policy": definition.get("rebalancing_policy"),
            "components": components,
            "initial_exposure": dict(definition.get("initial_exposure") or {}),
        }
    raise ValueError("Only approved filtered and composite definitions can be summarized")


def reference_onboarding_programme(
    *,
    subject_states: Mapping[str, str],
    onboarding_states: Mapping[str, str] | None = None,
    discovered_account_ids: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Return the ordered self-service programme and its server-side gates.

    The sequence is intentionally stricter than the dependency graph: each
    reference trial must be fully active before the next can start.  This makes
    the website and API enforce the owner's approved rollout order rather than
    relying on operators to remember it.
    """

    cases = dict(onboarding_states or {})
    discovered = {str(value).strip().upper() for value in discovered_account_ids}
    result: list[dict[str, Any]] = []
    for position, raw in enumerate(REFERENCE_ONBOARDING_PROGRAMME, start=1):
        item = dict(raw)
        subject_id = str(item["subject_id"])
        lifecycle = subject_states.get(subject_id)
        case_status = cases.get(subject_id)
        predecessor = str(item["predecessor_subject_id"])
        required = tuple(str(value) for value in item["required_subject_ids"])
        missing_active = [
            value for value in required if subject_states.get(value) != "active"
        ]
        source_available = not (
            item["subject_type"] == "master_account"
            and subject_id.upper() not in discovered
        )
        if lifecycle == "active" or case_status == "active":
            state = "complete"
            reason = "This subject is active and the next programme stage may proceed."
        elif case_status in {"draft", "in_progress", "blocked", "ready"}:
            state = "in_progress"
            reason = "Continue the existing page-by-page onboarding case."
        elif missing_active:
            state = "locked"
            reason = "Waiting for active prerequisite(s): " + ", ".join(missing_active)
        elif not source_available:
            state = "locked"
            reason = "The master account must first be discovered from a validated source."
        else:
            state = "ready"
            reason = "All predecessor and source gates have passed."
        item.update(
            {
                "position": position,
                "state": state,
                "reason": reason,
                "lifecycle_status": lifecycle,
                "case_status": case_status,
                "can_start": state == "ready",
            }
        )
        result.append(item)
    return result


def onboarding_branch_catalog() -> list[dict[str, Any]]:
    """Describe the governed branches exposed by the self-service wizard."""

    return [
        {
            "subject_type": "physical_account",
            "title": "Physical account",
            "status": "wave_0_complete",
            "description": (
                "Import one broker-reported account, reconcile its history and "
                "activate analytics only after all quality gates pass."
            ),
            "requires": [
                "validated_ibkr_connection",
                "discovered_source_account",
                "financial_contract",
                "report_documents",
            ],
        },
        {
            "subject_type": "master_account",
            "title": "Master account",
            "status": "after_composite_tests",
            "description": (
                "Register an administrative master or create a consolidated view "
                "of effective-dated children without double counting master totals."
            ),
            "requires": [
                "validated_ibkr_connection",
                "master_reporting_mode",
                "child_membership",
                "aggregation_reconciliation",
            ],
        },
        {
            "subject_type": "filtered_portfolio",
            "title": "Filtered derived portfolio",
            "status": "next_test",
            "description": (
                "Create an audited position filter and reconstructed cash ledger, "
                "as used by China & Hong Kong."
            ),
            "requires": [
                "active_source_account",
                "position_filter",
                "cash_ledger_policy",
                "historical_dry_run",
            ],
        },
        {
            "subject_type": "composite_portfolio",
            "title": "Fixed-unit composite",
            "status": "after_filtered_test",
            "description": (
                "Combine accounts, portfolios and benchmarks through a typed "
                "normalization formula, as used by Alpha World MS and Alpha World T."
            ),
            "requires": [
                "active_components",
                "normalization_contract",
                "fx_and_price_conventions",
                "exposure_preview",
            ],
        },
    ]
