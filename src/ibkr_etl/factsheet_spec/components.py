"""Qualified code components a design spec may use, and nothing else.

A spec can't carry code. When a design needs something richer than text, fills,
lines and tables (a NAV chart, the Alpha World return distribution, the T native
font layer), it names one of these components and passes it parameters. Each one
is reviewed code in this repository and most simply call the hand-written
renderers' own drawing functions, so a spec-rendered page draws exactly what they
draw. A new component arrives the same way as any other code change: a pull
request, CI, review and a release.

Layers:
- ``overlay``: draws on the page's overlay canvas, ``fn(c, ctx, params, box)``;
- ``page``: changes the page itself, ``fn(page, ctx, params, state)``;
- ``document``: runs once on the finished writer and may return PDF metadata;
- ``source``: checks the template and completed example before rendering;
- ``validator``: reads the written PDF back and raises when it is wrong.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping

from reportlab.lib.colors import HexColor, black

from . import bindings
from .. import monthly_artifacts as legacy


class ComponentError(ValueError):
    pass


@dataclass(frozen=True)
class Component:
    name: str
    layer: str
    fn: Callable[..., Any]
    params: tuple[str, ...] = ()
    description: str = ""


REGISTRY: dict[str, Component] = {}


def component(name: str, layer: str, params: tuple[str, ...] = (), description: str = ""):
    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        REGISTRY[name] = Component(name, layer, fn, params, description)
        return fn
    return register


def _text(value: Any, ctx: bindings.Context) -> Any:
    """Component parameters may be templates over bindings and owner text; lists are resolved item by item."""
    if isinstance(value, str):
        return bindings.render_text(value, ctx)
    if isinstance(value, list):
        return tuple(_text(item, ctx) for item in value)
    return value


def _metrics(ctx: bindings.Context) -> Mapping[str, Any]:
    if ctx.inputs.chart_metrics is not None:
        return ctx.inputs.chart_metrics
    return legacy._alpha_world_metric_values(dict(ctx.snapshot))


# ---- overlay components ----------------------------------------------------------------------

@component("account.nav_line_chart", "overlay", (),
           "The account design's normalized NAV line with dated gridlines, in the box given.")
def _account_nav_chart(c, ctx, params, box):
    legacy._draw_account_nav_chart(c, ctx.inputs.normalized_nav, box=tuple(box))


@component("alpha_world.line_chart", "overlay",
           ("value_field", "title", "y_label", "baseline_label", "authority_style", "value_scale",
            "avoid_date_label_overlap"),
           "The Alpha World capital path or rebased-to-100 line chart.")
def _alpha_world_line(c, ctx, params, box):
    legacy._draw_alpha_world_line_chart(
        c, ctx.inputs.normalized_nav, box=tuple(box),
        value_field=str(params.get("value_field", "value")),
        title=_text(params.get("title", ""), ctx), y_label=_text(params.get("y_label", ""), ctx),
        baseline_label=_text(params.get("baseline_label", ""), ctx),
        authority_style=bool(params.get("authority_style", False)),
        value_scale=float(params.get("value_scale", 1.0)),
        avoid_date_label_overlap=bool(params.get("avoid_date_label_overlap", False)),
    )


@component("alpha_world.return_distribution", "overlay", ("authority_style",),
           "The bootstrap annualized return distribution with the normal fit and markers.")
def _alpha_world_distribution(c, ctx, params, box):
    legacy._draw_alpha_world_distribution(
        c, ctx.inputs, dict(_metrics(ctx)), box=tuple(box),
        **({"authority_style": bool(params["authority_style"])} if "authority_style" in params else {}),
    )


@component("alpha_world_ms.date_header", "overlay", ("date", "baseline_y"),
           "The MS 'As of' header, right-aligned on the shared page edge.")
def _ms_date_header(c, ctx, params, box):
    legacy._draw_alpha_world_ms_date_header(c, _text(params["date"], ctx), float(params["baseline_y"]))


def _labels(params, ctx):
    return {
        "parents": _text(params["parents"], ctx),
        "headings": _text(params["headings"], ctx),
        "captions": tuple(_text(lines, ctx) for lines in params["captions"]),
        "branches": _text(params["branches"], ctx),
    }


@component("alpha_world_ms.allocation_diagram", "overlay", ("parents", "headings", "captions", "branches"),
           "The MS two-level allocation diagram, cleared and redrawn with the labels given.")
def _ms_allocation(c, ctx, params, box):
    legacy._draw_alpha_world_ms_allocation(c, _labels(params, ctx))


@component("alpha_world_t.allocation_diagram", "overlay", ("parents", "headings", "captions", "branches"),
           "The T allocation diagram on the MS geometry, with the labels given.")
def _t_allocation(c, ctx, params, box):
    from .. import alpha_world_t_factsheet as t
    t._allocation(c, _labels(params, ctx))


@component("alpha_world_ms.notes", "overlay", (),
           "MS coverage and short-history warnings, printed only when the calculation evidence calls for them.")
def _ms_notes(c, ctx, params, box):
    inputs = ctx.inputs
    evidence = inputs.chart_evidence
    c.setFillColor(legacy.MUTED)
    c.setFont("Helvetica", 6.0)
    gaps = evidence["excluded_intervals"]
    if gaps:
        missing = sum(len(g["missing_sessions"]) for g in gaps)
        c.setFillColor(HexColor("#9b5d13"))
        c.drawString(42, 314, f"Coverage warning: {missing} missing session(s); {len(gaps)} multi-session interval(s) excluded from daily risk and MC. No prices imputed.")
    elif inputs.data_as_of != inputs.period_end:
        c.drawString(42, 314, f"Reporting month-end: {inputs.period_end}; last actual valuation: {inputs.data_as_of}. No month-end prices imputed.")
    c.setFillColor(legacy.MUTED)
    c.setFont("Helvetica", 6.0)
    if evidence["short_history"]:
        c.drawString(80, 58, "Limited history: indicative bootstrap only, not a forecast; parameter uncertainty is not shown.")


@component("alpha_world_t.notes", "overlay", (),
           "T coverage and short-history warnings, printed only when the calculation evidence calls for them.")
def _t_notes(c, ctx, params, box):
    inputs = ctx.inputs
    c.setFillColor(HexColor('#9b5d13')); c.setFont('Helvetica', 5.5)
    gaps = inputs.chart_evidence['excluded_intervals']
    if gaps:
        count = sum(len(g['missing_sessions']) for g in gaps)
        c.drawString(42., 310., f'Coverage warning: {count} missing session(s); multi-session intervals excluded from daily risk/MC. No prices imputed.')
    elif inputs.period_end != inputs.data_as_of:
        c.drawString(42., 310., f'Month-end: {inputs.period_end}; last actual valuation: {inputs.data_as_of}. No prices imputed.')
    if inputs.chart_evidence['short_history']:
        c.setFillColor(HexColor('#5d6878')); c.setFont('Helvetica', 5.5)
        c.drawString(80., 62., 'Limited history: indicative bootstrap only, not a forecast; parameter uncertainty is not shown.')


@component("alpha_world_t.synthetic_label", "overlay", (),
           "Marks test renders made from synthetic data; prints nothing for real data.")
def _t_synthetic(c, ctx, params, box):
    from .. import alpha_world_t_factsheet as t
    t._footer(c, 0, synthetic=(ctx.snapshot or {}).get("synthetic_data") is True)


@component("allocation_tree", "overlay", (),
           "A composite's long, short and cash sleeves as a two-level tree, in the box given.")
def _allocation_tree(c, ctx, params, box):
    from .. import factsheet_tree
    from ..report_profiles import snapshot_profile
    profile = snapshot_profile(dict(ctx.snapshot))
    if profile is None or not profile.composition:
        raise ComponentError("This snapshot has no composite composition to draw")
    factsheet_tree.draw(c, profile.composition, tuple(box) if box else factsheet_tree.AREA)


# ---- page components -------------------------------------------------------------------------

@component("alpha_world_t.static_sections", "page", (),
           "Merges the approved MS static sections (headings, rules, footer) onto a T page.")
def _t_sections(page, ctx, params, state):
    from ..alpha_world_t_sections import load_style, merge_static_sections
    style = state.setdefault("t_style", load_style())
    merge_static_sections(page, style, state["page_number"])


@component("alpha_world_t.native_text", "page", (),
           "Draws the T identity text in the template's own embedded fonts.")
def _t_native(page, ctx, params, state):
    from ..alpha_world_t_sections import load_style, add_native_text
    style = state.setdefault("t_style", load_style())
    add_native_text(page, style, state["page_number"])


@component("alpha_world_ms.remove_date_text", "page", ("box",),
           "Removes the completed example's date glyphs, so copied text matches the visible header.")
def _ms_remove_date(page, ctx, params, state):
    legacy._remove_alpha_world_ms_source_date_text(page, tuple(params["box"]))


# ---- document components ---------------------------------------------------------------------

@component("alpha_world_ms.copy", "document", (), "Repairs the MS disclaimer copy and records its version.")
def _ms_copy(writer, ctx, params, state):
    from ..alpha_world_ms_copy import COPY_SHA256, COPY_VERSION, repair_disclaimers
    repair_disclaimers(writer.pages)
    return {"/MSCopyVersion": COPY_VERSION, "/MSCopySHA256": COPY_SHA256}


@component("alpha_world_ms.subtitle", "document", (), "Applies the approved MS subtitle.")
def _ms_subtitle(writer, ctx, params, state):
    from ..alpha_world_ms_subtitle import apply, VERSION
    apply(writer.pages[0])
    return {"/MSSubtitleVersion": VERSION}


@component("alpha_world_ms.calculation_metadata", "document", (),
           "Records the MS calculation version, evidence and metrics in the PDF.")
def _ms_metadata(writer, ctx, params, state):
    import json
    inputs = ctx.inputs
    return {"/MSCalculationVersion": inputs.chart_evidence["version"],
            "/MSCalculationEvidence": json.dumps(inputs.chart_evidence, sort_keys=True, allow_nan=False),
            "/MSMetrics": json.dumps(inputs.chart_metrics, sort_keys=True, allow_nan=False)}


@component("alpha_world_t.calculation_metadata", "document", (),
           "Records the T methodology, evidence, metrics and snapshot hash in the PDF.")
def _t_metadata(writer, ctx, params, state):
    import hashlib
    import json
    from ..alpha_world_t_analytics import methodology_identity
    from ..alpha_world_t_sections import STYLE_SHA256
    inputs, snapshot = ctx.inputs, ctx.snapshot
    canonical = json.dumps(snapshot, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return {'/TMSStyleSHA256': STYLE_SHA256,
            '/TMethodologyIdentity': json.dumps(methodology_identity(), sort_keys=True),
            '/TCalculationEvidence': json.dumps(inputs.chart_evidence, sort_keys=True, allow_nan=False),
            '/TMetrics': json.dumps(inputs.chart_metrics, sort_keys=True, allow_nan=False),
            '/TSnapshotSHA256': hashlib.sha256(canonical).hexdigest(), '/PublicationAllowed': 'false',
            '/SyntheticData': str(snapshot.get('synthetic_data') is True).lower()}


# ---- source checks ---------------------------------------------------------------------------

@component("monthly_artifacts.design_sources", "source", (),
           "The template and completed example are both two-page PDFs of the approved size.")
def _design_sources(paths, ctx, params, state):
    if paths.get("example") is None:
        return {}
    try:
        return legacy.validate_factsheet_design_sources(template_path=paths["template"],
                                                        design_model_path=paths["example"])
    except legacy.MonthlyArtifactError as error:
        raise ComponentError(str(error)) from error


@component("alpha_world.source_layout", "source", (),
           "The Alpha World template and completed example carry the approved static layout.")
def _alpha_world_source(paths, ctx, params, state):
    legacy._validate_alpha_world_source_layout(subject_id=ctx.inputs.subject_id, template_path=paths["template"],
                                               design_model_path=paths["example"])
    return {}


# ---- validators ------------------------------------------------------------------------------

@component("monthly_artifacts.account_layout", "validator", (),
           "The hand-written account layout validator, kept while the account design is qualified.")
def _account_layout(path, ctx, params, state):
    legacy.validate_factsheet_layout(path, ctx.inputs)


@component("monthly_artifacts.alpha_world_layout", "validator", (),
           "The hand-written Alpha World layout validator.")
def _alpha_world_layout(path, ctx, params, state):
    legacy.validate_alpha_world_factsheet_layout(path, ctx.inputs)


@component("alpha_world_ms.copy_check", "validator", (), "The MS disclaimer copy is the approved version.")
def _ms_copy_check(path, ctx, params, state):
    from ..alpha_world_ms_copy import validate_copy
    validate_copy(path)


@component("alpha_world_ms.pdf_accuracy", "validator", (),
           "Every MS figure in the PDF reconciles to an independent recalculation from the snapshot.")
def _ms_accuracy(path, ctx, params, state):
    from ..alpha_world_ms_pdf_validation import validate
    try:
        state["pdf_accuracy_validation"] = validate(path, dict(ctx.snapshot))
    except (ValueError, StopIteration) as error:
        raise legacy.MonthlyArtifactError(str(error)) from error


@component("alpha_world_t.pdf_accuracy", "validator", (),
           "Every T figure and the stripped regions reconcile to the snapshot and the owner template.")
def _t_accuracy(path, ctx, params, state):
    from ..alpha_world_t_pdf_validation import validate
    state["pdf_accuracy_validation"] = validate(path, dict(ctx.snapshot), template_path=state["paths"]["template"])


# ---- inputs ----------------------------------------------------------------------------------

def inputs_builder(kind: str, currencies: tuple[str, ...]) -> Callable[[dict[str, Any]], Any]:
    """How a design reads the snapshot: the account rules, or MS or T's own methodology."""
    if kind == "account":
        return lambda snapshot: legacy.factsheet_inputs(snapshot, reporting_currencies=currencies)
    if kind == "alpha_world_ms":
        def ms(snapshot):
            if snapshot.get("subject_id") != "alpha-world-ms":
                raise legacy.MonthlyArtifactError("The MS methodology reads the Alpha World MS snapshot only")
            return legacy.factsheet_inputs(snapshot)
        return ms
    if kind == "alpha_world_t":
        return _t_inputs
    raise ComponentError(f"Unknown inputs: {kind}")


def _t_inputs(snapshot):
    import math
    from ..alpha_world_t_analytics import analyze
    from ..monthly_artifacts import FactsheetInputs, MonthlyArtifactError, _normalize_nav, _trailing_twelve_month_return
    if snapshot.get('publication_allowed') is not False:
        raise MonthlyArtifactError('T template is private-qualification only; publication is not approved')
    try:
        metrics, daily, evidence = analyze(snapshot)
    except (ValueError, KeyError, TypeError) as exc:
        raise MonthlyArtifactError(str(exc)) from exc
    normalized = _normalize_nav(snapshot['performance']['series'])
    if not math.isclose(normalized[0]['nav'], 100., abs_tol=1e-9, rel_tol=0.):
        raise MonthlyArtifactError('T inception NAV must reconcile to the approved 100')
    if not all(math.isfinite(v) for v in metrics.values() if v is not None):
        raise MonthlyArtifactError('T metrics are not finite')
    return FactsheetInputs(
        subject_id='alpha-world-t', reporting_currency='HKD', is_composite=True,
        history_start_date=snapshot['period_start'], period_end=snapshot['period_end'],
        data_as_of=snapshot['data_as_of'], display_date=snapshot['period_end'], nav=normalized[-1]['nav'],
        positions=5, gross_market_value=0., top_holdings=(), sectors=(), history=(), normalized_nav=normalized,
        trailing_twelve_month_return=_trailing_twelve_month_return(normalized, snapshot['period_end']),
        chart_metrics=metrics, chart_daily_returns=daily, chart_evidence=evidence)


def describe() -> list[dict[str, Any]]:
    """The component catalogue, as the agents and the review page see it."""
    return [{"name": c.name, "layer": c.layer, "params": list(c.params), "description": c.description}
            for c in sorted(REGISTRY.values(), key=lambda c: c.name)]
