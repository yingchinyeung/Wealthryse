"""Find where the Metrics guide has fallen behind the website.

Two checks, both read only the repository's source files (no client data):

* Labels: every figure label the website renders (metric cards, Modelling
  tiles, the what-if table) must be a guide name, one of its aliases, or be
  mapped in ``LABEL_MAP``. A new card with no definition shows up here.
* Calculations: each function in ``WATCHED_SOURCES`` is fingerprinted from its
  syntax tree (comments, docstrings and layout ignored; the module constants it
  reads are included). ``metrics_guide_sources.json`` records the fingerprints
  the guide was last checked against, so a changed calculation names the
  definitions to re-read.

``scripts/check_metrics_guide.py`` runs the audit in CI as a warning. On Azure, the weekly
``guide_keeper`` agent job runs it on the deployed code, has the analyst model draft the update from
the facts in ``drift_facts`` and posts the draft to the Agent inbox for the owner.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from ibkr_etl import metrics_guide as guide

# The directory holding the ibkr_etl and wealthryse_agent packages: src/ in the repository, or
# site-packages in the agent image.
ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_name("metrics_guide_sources.json")

# (file under src/ibkr_etl, start marker, end marker, label pattern). With no
# markers the whole file is scanned.
LABEL_SOURCES = (
    ("web.py", "riskItems=[[", "]];", r"\['([^'#][^']*)',"),
    ("web.py", "items=[['Portfolio total return'", "]];", r"\['([^'#][^']*)',"),
    ("web.py", "msItems.push(", ");", r"\['([^'#][^']*)',"),
    ("web_analytics_ui.py", None, None, r"\['([^'#][^']*)',"),
    ("web_modelling.py", None, None, r"modelTile\('([^']+)'"),
    ("web_modelling.py", "const whatifRows=[", "];", r"\['([^']+)',"),
)

# Website wording that differs from the guide entry which explains it.
LABEL_MAP = {
    "90% range held the outcome": "Outlook score",
    "Middle 50% range held it": "Outlook score",
    "Direction right": "Outlook score",
    "Directional skill": "Outlook score",
    "Regime now": "Market regime",
    "Chance of a loss over 3 months": "Regime outlook range",
    "Alpha (a year)": "Alpha (factor model)",
    "Weeks used": "Factor beta",
    "Forecast volatility (next month, a year)": "Forecast volatility",
    "One-day VaR (95%)": "Forward VaR",
    "One-day VaR (99%)": "Forward VaR",
    "One-month VaR (95%)": "Forward VaR",
    "One-day expected tail loss (95%)": "Forward expected tail loss",
    "One-month expected tail loss (95%)": "Forward expected tail loss",
    "Strong case (95th percentile)": "Wealth projection range",
    "Weak case (5th percentile)": "Wealth projection range",
    "In money": "Wealth projection range",
    "Total contributions": "Wealth projection range",
    "Volatility used": "Projection return and volatility used",
    "Annual return (historical)": "What-if comparison",
    "Volatility": "What-if comparison",
    "Daily VaR (95%)": "What-if comparison",
    "Daily expected tail loss (95%)": "What-if comparison",
    "Maximum drawdown": "What-if comparison",
    "Beta to benchmark": "What-if comparison",
    "Cash": "What-if comparison",
}

# Calculation functions ("module:function") and the definitions that describe them.
WATCHED_SOURCES = {
    "ibkr_etl.performance_periods:period_return": ("Time-weighted return (TWR)", "TWR annualised", "Net external flows"),
    "ibkr_etl.performance_periods:xirr": ("IRR annualised",),
    "ibkr_etl.benchmark_analytics:portfolio_risk_metrics": (
        "Total return", "Annualized return", "Annualized volatility", "Sharpe ratio", "VaR 95% — daily",
        "Daily Expected Tail Loss (95%)", "Win rate", "BLUE mean daily return"),
    "ibkr_etl.benchmark_analytics:_one_year_drawdown_metrics": (
        "Expected maximum drawdown (1 year)", "Actual maximum drawdown (1 year)", "Tail maximum drawdown (1 year)"),
    "ibkr_etl.benchmark_analytics:expected_tail_loss_contributors": (
        "Expected Tail Loss contribution", "Top 5 ETL contributors", "Bottom 5 ETL contributors",
        "ETL residual contribution"),
    "ibkr_etl.benchmark_analytics:comparison_metrics": (
        "Portfolio total return", "Benchmark total return", "Excess total return", "Tracking error",
        "Information ratio", "Beta", "Correlation", "Relative win rate"),
    "ibkr_etl.portfolio_analytics:daily_return_series": ("Cash-flow-adjusted daily portfolio return",),
    "ibkr_etl.portfolio_analytics:monthly_calendar": ("Monthly return",),
    "ibkr_etl.portfolio_analytics:drawdown_analysis": (
        "Maximum drawdown (since inception)", "Current drawdown", "Drawdown episode"),
    "ibkr_etl.portfolio_analytics:return_statistics": (
        "Sortino ratio", "Calmar ratio", "Downside deviation", "Skewness", "Excess kurtosis", "Best and worst day",
        "Best and worst month", "Positive months", "Average gain and loss month", "Gain/loss ratio"),
    "ibkr_etl.portfolio_analytics:rolling_series": ("Rolling 3-month volatility", "Trailing 12-month return"),
    "ibkr_etl.portfolio_analytics:rolling_benchmark_statistics": ("Rolling beta and correlation",),
    "ibkr_etl.portfolio_analytics:holdings_concentration": (
        "Largest holding", "Top 5 weight", "Top 10 weight", "Effective number of holdings"),
    "ibkr_etl.portfolio_analytics:trading_activity": ("Trading activity", "Portfolio turnover", "Commissions / NAV"),
    "ibkr_etl.portfolio_analytics:holdings_correlation": ("Holdings correlation",),
    "ibkr_etl.portfolio_analytics:wilder_rsi": ("RSI (14)",),
    "ibkr_etl.crisis_scenarios:estimate_beta": ("Crisis scenario stand-ins (scaled)",),
    "ibkr_etl.crisis_scenarios:combine": ("Crisis scenario maximum drawdown",),
    "ibkr_etl.modelling:project_wealth": (
        "Wealth projection range", "Chance of reaching the target", "Chance of running out",
        "Chance of ending below money put in", "Projection return and volatility used"),
    "ibkr_etl.modelling:what_if": ("What-if comparison", "Worst month (21 sessions)"),
    "ibkr_etl.modelling:risk_contributions": ("Share of risk",),
    "ibkr_etl.modelling:optimise": (
        "Expected return (optimisation)", "Minimum variance portfolio", "Risk parity portfolio",
        "Maximum Sharpe portfolio", "Efficient frontier"),
    "ibkr_etl.modelling:factor_model": (
        "Factor beta", "Factor t-statistic", "Alpha (factor model)", "Explained by factors (R²)",
        "Unexplained volatility", "Factor return contribution"),
    "ibkr_etl.risk_forecasting:fit_volatility": ("Forecast volatility", "Long-run volatility", "Shock half-life"),
    "ibkr_etl.risk_forecasting:volatility_forecast": ("Forward VaR", "Forward expected tail loss"),
    "ibkr_etl.risk_forecasting:var_backtest": ("VaR backtest",),
    "ibkr_etl.risk_forecasting:drawdown_odds": ("Drawdown odds (next 12 months)",),
    "ibkr_etl.risk_forecasting:factor_stress": ("Factor stress test impact", "Typical unexplained move in a month"),
    "ibkr_etl.risk_forecasting:choose_regimes": ("Market regime",),
    "ibkr_etl.risk_forecasting:regime_outlook": ("Regime outlook range", "Outlook score"),
    "ibkr_etl.export_agent:book_checks": (
        "Last value matches the books", "Holdings and cash add up to the account's value",
        "Cash balances roll forward", "No long gaps in the value history"),
    "ibkr_etl.export_agent:file_checks": ("Totals tie to the file",),
    "ibkr_etl.export_agent:overall": ("Export check result",),
    "wealthryse_agent.jobs:risk_triggers": ("Risk alert",),
    "wealthryse_agent.jobs:model_check_findings": ("Risk model check",),
}


def _normal(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).strip()


def _guide_terms() -> dict[str, str]:
    terms = {_normal(name): name for name in guide.METRIC_ANCHORS}
    for name, aliases in guide.METRIC_ALIASES.items():
        for alias in aliases:
            terms.setdefault(_normal(alias), name)
    return terms


def metric_for_label(label: str) -> str | None:
    """The guide definition that explains a website label, if there is one."""

    if label in LABEL_MAP:
        return LABEL_MAP[label]
    return _guide_terms().get(_normal(label))


def website_labels(root: Path = ROOT) -> dict[str, str]:
    """Figure labels the website renders, each with the file it came from."""

    found: dict[str, str] = {}
    for name, start, end, pattern in LABEL_SOURCES:
        path = root / "ibkr_etl" / name
        source = path.read_text(encoding="utf-8")
        chunks = [source]
        if start is not None:
            chunks, position = [], 0
            while (begin := source.find(start, position)) != -1:
                finish = source.find(end, begin + len(start))
                finish = len(source) if finish == -1 else finish
                chunks.append(source[begin:finish])
                position = finish
        for chunk in chunks:
            for label in re.findall(pattern, chunk):
                found.setdefault(label, f"src/ibkr_etl/{name}")
    return found


def _module_path(root: Path, module: str) -> Path:
    return root / Path(*module.split(".")).with_suffix(".py")


def _strip_docstrings(node: ast.AST) -> ast.AST:
    for item in ast.walk(node):
        body = getattr(item, "body", None)
        if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and body
                and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            item.body = body[1:] or [ast.Pass()]
    return node


def function_source(root: Path, source: str) -> str:
    """The current source text of a watched function."""

    module, function = source.split(":")
    text = _module_path(root, module).read_text(encoding="utf-8")
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            return ast.get_source_segment(text, node) or ""
    raise LookupError(f"{source} not found")


def _canonical(node: object) -> str:
    """``ast.dump`` without empty fields, so the hash is the same on every Python version.

    Python 3.13 stopped printing ``None`` and ``[]`` fields that 3.12 prints, and new versions add
    fields with empty defaults (``type_params``); leaving all empty fields out keeps both stable.
    """

    if isinstance(node, ast.AST):
        fields = []
        for name, value in ast.iter_fields(node):
            if value is None or value == []:
                continue
            fields.append(f"{name}={_canonical(value)}")
        return f"{type(node).__name__}({', '.join(fields)})"
    if isinstance(node, list):
        return "[" + ", ".join(_canonical(item) for item in node) + "]"
    return repr(node)


def fingerprint(root: Path, source: str) -> str:
    """Hash of a function's syntax tree plus the module constants it reads."""

    module, function = source.split(":")
    tree = ast.parse(_module_path(root, module).read_text(encoding="utf-8"))
    constants: dict[str, ast.AST] = {}
    target = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            target = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for item in targets:
                if isinstance(item, ast.Name) and item.id.isupper() and node.value is not None:
                    constants[item.id] = node.value
    if target is None:
        raise LookupError(f"{source} not found")
    _strip_docstrings(target)
    used = sorted({n.id for n in ast.walk(target) if isinstance(n, ast.Name) and n.id in constants})
    parts = [_canonical(target)]
    parts += [f"{name}={_canonical(constants[name])}" for name in used]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def current_fingerprints(root: Path = ROOT) -> dict[str, str]:
    return {source: fingerprint(root, source) for source in sorted(WATCHED_SOURCES)}


def load_manifest(path: Path = MANIFEST) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(json.loads(path.read_text(encoding="utf-8")).get("sources", {}))


def write_manifest(root: Path = ROOT, path: Path = MANIFEST) -> dict[str, str]:
    """Record that the guide has been checked against today's calculations."""

    sources = current_fingerprints(root)
    payload = {
        "about": "Fingerprints of the calculations the Metrics guide was last checked against. "
                 "Refresh with scripts/check_metrics_guide.py --refresh after updating the guide.",
        "sources": sources,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return sources


@dataclass
class AuditReport:
    uncovered_labels: dict[str, str] = field(default_factory=dict)
    changed_sources: dict[str, tuple[str, ...]] = field(default_factory=dict)
    unrecorded_sources: list[str] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.uncovered_labels or self.changed_sources or self.unrecorded_sources
                    or self.missing_sources)

    def as_dict(self) -> dict:
        return {"clean": self.clean, "uncovered_labels": self.uncovered_labels,
                "changed_sources": {k: list(v) for k, v in self.changed_sources.items()},
                "unrecorded_sources": self.unrecorded_sources, "missing_sources": self.missing_sources}


def audit(root: Path = ROOT, manifest: Path = MANIFEST) -> AuditReport:
    report = AuditReport()
    for label, path in sorted(website_labels(root).items()):
        if metric_for_label(label) is None:
            report.uncovered_labels[label] = path
    recorded = load_manifest(manifest)
    for source, metrics in sorted(WATCHED_SOURCES.items()):
        try:
            current = fingerprint(root, source)
        except (LookupError, FileNotFoundError):
            report.missing_sources.append(source)
            continue
        if source not in recorded:
            report.unrecorded_sources.append(source)
        elif recorded[source] != current:
            report.changed_sources[source] = metrics
    return report


def brief(report: AuditReport, root: Path = ROOT) -> str:
    """Markdown instructions for the agent that drafts the guide update."""

    lines = ["# Metrics guide drift", ""]
    if report.clean:
        return "\n".join(lines + ["Nothing to update: the guide matches the website."])
    if report.uncovered_labels:
        lines += ["## Labels on the website with no definition", ""]
        lines += [f"- `{label}` ({path})" for label, path in report.uncovered_labels.items()]
        lines += ["", "Add a definition to `src/ibkr_etl/metrics_guide.py` for each new figure, or map "
                  "wording that an existing definition already explains in `LABEL_MAP` in "
                  "`src/ibkr_etl/metrics_guide_audit.py`.", ""]
    if report.changed_sources:
        lines += ["## Calculations that changed since the guide was last checked", ""]
        for source, metrics in report.changed_sources.items():
            lines += [f"### `{source}`", "", "Definitions to re-read: " + ", ".join(f"\"{m}\"" for m in metrics), "",
                      "```python", function_source(root, source), "```", ""]
            for name in metrics:
                lines += [f"Current definition of \"{name}\":", "",
                          "```text", definition_text(name), "```", ""]
    if report.unrecorded_sources or report.missing_sources:
        lines += ["## Watch list", ""]
        lines += [f"- Not yet recorded: `{source}`" for source in report.unrecorded_sources]
        lines += [f"- No longer found (renamed or removed): `{source}`" for source in report.missing_sources]
        lines += ["", "Fix `WATCHED_SOURCES` in `src/ibkr_etl/metrics_guide_audit.py` for anything renamed.", ""]
    lines += ["## When the guide is right", "",
              "Run `python scripts/check_metrics_guide.py --refresh` to record the new fingerprints, then "
              "`python -m unittest tests.test_metrics_guide tests.test_metrics_guide_audit`."]
    return "\n".join(lines)


_FIGURE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")


def drift_key(report: AuditReport) -> str:
    """A short fingerprint of what drifted, so one drift gets one inbox card."""

    return hashlib.sha256(json.dumps(report.as_dict(), sort_keys=True).encode("utf-8")).hexdigest()[:16]


def drift_facts(report: AuditReport, root: Path = ROOT) -> dict:
    """What a drafting agent needs: each drift item with the code and the current definitions.

    ``figures_in_source`` lists every number in that code and those definitions, so a drafted
    definition may quote them and nothing else.
    """

    figures: set[float] = set()
    labels = [{"label": label, "file": path} for label, path in report.uncovered_labels.items()]
    changed = []
    for source, metrics in report.changed_sources.items():
        code = function_source(root, source)
        definitions = [{"name": name, **_definition_fields(name)} for name in metrics]
        figures.update(float(x) for x in _FIGURE.findall(code))
        for item in definitions:
            figures.update(float(x) for value in item.values() for x in _FIGURE.findall(str(value)))
        changed.append({"function": source, "code": code, "definitions": definitions})
    return {"labels_without_definition": labels, "changed_calculations": changed,
            "watch_list_not_recorded": report.unrecorded_sources, "watch_list_missing": report.missing_sources,
            "sections": [{"key": s.key, "title": s.title, "where": s.location} for s in guide.METRIC_SECTIONS],
            "figures_in_source": sorted(figures)}


def _definition_fields(name: str) -> dict:
    metric = guide.definition(name)
    return {"what_it_tells_you": metric.meaning, "technical_method": metric.calculation,
            "how_to_explain_it": metric.interpretation, "how_we_work_it_out": guide.CLIENT_CALCULATIONS[name]}


def definition_text(name: str) -> str:
    return "\n".join(f"{key}: {value}" for key, value in _definition_fields(name).items())
