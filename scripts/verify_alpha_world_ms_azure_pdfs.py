"""Verify cloud MS PDFs against NAV-based calculations and reviewed v14 geometry."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import random
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageChops, ImageDraw
from pypdf import PdfReader
from reportlab.pdfbase.pdfmetrics import stringWidth
import numpy as np
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ibkr_etl.monthly_artifacts import (
    ALPHA_WORLD_FACTSHEET_LAYOUT, ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION,
    _collect_text_runs, factsheet_inputs, validate_alpha_world_factsheet_layout,
)
from ibkr_etl.alpha_world_ms_analytics import analyze, simulate
from ibkr_etl.alpha_world_ms_pdf_validation import validate

CLOUD = ROOT / "output/pdf/AlphaWorld_MS_Azure_Factsheets_v14r7"
LOCAL = ROOT / "output/pdf/AlphaWorld_MS_Monthly_Factsheets_v14r7"
CAPTURE = ROOT / ".work/alpha-ms-factsheets-v13-20261007"
QA = ROOT / "tmp/pdfs/alpha-ms-azure-v14r7"
REMOVED = ('NAV basis:', 'Daily risk:', '1,000 bootstrap paths;',
           'Historical marker: inception CAGR.',
           'Inception fixed-unit structure, not month-end weights.')


def sha(content):
    return hashlib.sha256(content).hexdigest()


def main():
    QA.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((CLOUD / "manifest.json").read_bytes())
    previous_folder = ROOT / 'output/pdf/AlphaWorld_MS_Azure_Factsheets_v14r5'
    previous_entries = {r['period_end']: r for r in json.loads((previous_folder/'manifest.json').read_bytes())['artifacts']}
    fields = ("annualized_return", "annualized_volatility", "sharpe_ratio", "maximum_drawdown",
              "var_95_daily", "cvar_95_daily", "win_rate", "total_return")
    baselines = ALPHA_WORLD_FACTSHEET_LAYOUT["page_1"]["metric_baselines"]

    def check(entry):
        period = entry["period_end"]
        path = CLOUD / Path(entry["blob_name"]).name
        recovered = period in {'2026-08-31', '2026-09-30'}
        raw = ((ROOT / '.work/alpha-ms-recovery-20261007/corrected-inputs' / (period + '.json'))
               if recovered else (CAPTURE / "snapshots" / (period + ".json"))).read_bytes()
        if sha(raw) != entry["data_sha256"] or sha(path.read_bytes()) != entry["sha256"]:
            raise ValueError("Cloud artifact or frozen-source digest mismatch")
        payload = json.loads(raw)
        old_entry = previous_entries[period]
        if entry['data_sha256'] != old_entry['data_sha256'] or entry['render']['chart_calculation'] != old_entry['render']['chart_calculation']:
            raise ValueError('Source input or chart calculations changed during the caption edit')
        document = PdfReader(path)
        if len(document.pages) != 2 or document.metadata["/ChartStyleVersion"] != ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION:
            raise ValueError("Cloud PDF page count or version mismatch")
        validate_alpha_world_factsheet_layout(path, factsheet_inputs(payload))
        accuracy = validate(path, payload)
        calculated, daily, evidence = analyze(payload)
        # Separate NumPy replay verifies the annualized 252-session NAV-return
        # bootstrap and that no second interest charge is deducted from paths.
        sample, mc_mean = simulate(daily, period)
        rng = random.Random(int(hashlib.sha256(
            f"alpha-world-ms|{period}|{evidence['version']}".encode()).hexdigest()[:16],16))
        independent_paths = np.asarray([np.prod([
            1+daily[rng.randrange(len(daily))] for _ in range(252)])-1 for _ in range(1000)])
        if len(sample)!=1000 or np.max(np.abs(independent_paths-np.asarray(sample)))>1e-11:
            raise ValueError('NAV-after-interest annualized simulation replay failed')
        if abs(float(np.mean(independent_paths))-mc_mean)>1e-11:
            raise ValueError('MC mean differs from independent NAV-interest basis')
        # Independent NumPy arithmetic, separately from the PDF geometry gate.
        nav = np.array([r['nav'] for r in payload['performance']['series']], dtype=float)
        returns = np.asarray(daily)
        sigma = np.std(returns, ddof=1)
        threshold = np.quantile(returns, .05)
        elapsed = (date.fromisoformat(evidence['end'])-date.fromisoformat(evidence['start'])).days
        independently = {'total_return':nav[-1]/nav[0]-1,
            'annualized_return':(nav[-1]/nav[0])**(365.25/elapsed)-1,
            'annualized_volatility':sigma*np.sqrt(252),
            'sharpe_ratio':(np.mean(returns)-((1+calculated['risk_free_annual_rate'])**(1/252)-1))/sigma*np.sqrt(252) if sigma else None,
            'maximum_drawdown':np.min(nav/np.maximum.accumulate(nav)-1),
            'var_95_daily':max(0.,-threshold),'cvar_95_daily':max(0.,-np.mean(returns[returns<=threshold])),
            'win_rate':np.mean(returns>0)}
        for field,value in independently.items():
            if value is None:
                if calculated[field] is not None: raise ValueError('Undefined Sharpe mislabeled')
            elif abs(value-calculated[field])>1e-11:
                raise ValueError('Independent NAV-based KPI reconciliation failed')
        runs = _collect_text_runs(document.pages[0])
        values = {}
        for field, baseline in zip(fields, baselines):
            value = calculated[field]
            if value is not None and not math.isfinite(float(value)):
                raise ValueError("Required financial figure unavailable")
            if field in {"var_95_daily", "cvar_95_daily"}:
                value = -abs(value)
            expected = ('N/A' if value is None else f"{value:.2f}" if field == "sharpe_ratio" else
                        f"{value:.2%}" if field == "win_rate" else f"{value:+.2%}")
            actual = [r[0] for r in runs if 516 <= r[1] <= 558 and abs(r[2] - baseline) < .15]
            if actual != [expected]:
                raise ValueError("Cloud KPI differs from independently reconciled NAV basis")
            values[field] = expected
        reference = PdfReader(LOCAL / path.name)
        previous_path = ROOT / 'output/pdf/AlphaWorld_MS_Azure_Factsheets_v14r5' / path.name
        previous = PdfReader(previous_path)
        for page, (actual, expected) in enumerate(zip(document.pages, reference.pages), 1):
            if _collect_text_runs(actual) != _collect_text_runs(expected):
                raise ValueError("Cloud PDF text/font geometry differs from reviewed local v14")
            old_runs = []
            for r in _collect_text_runs(previous.pages[page-1]):
                if page == 1 and r[0] == 'MC One-Year Return Distribution' and abs(r[2]-235)<.001 and r[3]==7.:
                    title = 'MC Annualized Return Distribution - After Interest'
                    r = (title,round(326.5-stringWidth(title,'Helvetica-Bold',7.)/2,6),*r[2:])
                if page == 2 and 399.5 <= r[1] <= 490.5 and r[3] == 8.:
                    if r[0] == 'Unadjusted price' and abs(r[2]-622)<.001:
                        continue
                    if r[0] == 'SPY' and abs(r[2]-634)<.001:
                        r = ('S&P500', round(445-stringWidth('S&P500','Helvetica',8.)/2,6), *r[2:])
                old_runs.append(r)
            if _collect_text_runs(actual) != old_runs:
                raise ValueError('Text, numbers, fonts or geometry changed beyond the approved allocation caption and distribution title')
        from ibkr_etl.alpha_world_ms_pdf_validation import _elements
        for actual, old in zip(document.pages, previous.pages):
            paths, boxes, _ = _elements(actual,document)
            old_paths, old_boxes, _ = _elements(old,previous)
            if paths != old_paths or boxes != old_boxes:
                raise ValueError('Chart vectors or layout rectangles changed during the SPY-caption edit')
        for kind, source in (("azure", path), ("local", LOCAL / path.name), ('previous',previous_path)):
            subprocess.run(["pdftoppm", "-scale-to", "1400", "-png", str(source),
                str(QA / (period + "-" + kind))], check=True, capture_output=True, timeout=60)
        pages = []
        for page in (1, 2):
            actual = Image.open(QA / f"{period}-azure-{page}.png").convert("RGB")
            expected = Image.open(QA / f"{period}-local-{page}.png").convert("RGB")
            difference = ImageChops.difference(actual, expected)
            if difference.getbbox() is not None:
                difference.save(QA / f"{period}-difference-{page}.png")
                raise ValueError("Cloud raster differs from reviewed local v14: " + period)
            old = Image.open(QA / f'{period}-previous-{page}.png').convert('RGB')
            changed = ImageChops.difference(actual,old)
            masks = ((405,619,485,643),) if page == 2 else ((210,232,445,244),)
            painter = ImageDraw.Draw(changed)
            for left,bottom,right,top in masks:
                painter.rectangle((left/595.276*actual.width,actual.height-top/841.89*actual.height,
                    right/595.276*actual.width,actual.height-bottom/841.89*actual.height),fill=(0,0,0))
            if changed.getbbox() is not None:
                raise ValueError('Cloud pixels changed outside the approved caption and title')
            pages.append({"page": page, "pixel_identical_to_reviewed_v14": True,
                          'unchanged_pixels_outside_approved_caption_and_title':True})
        return {"period_end": period, "sha256": entry["sha256"], "kpis": values,
                "frozen_hash_verified": True, "layout_geometry_verified": True, "pdf_vector_accuracy":accuracy,
                "coverage_warnings":evidence['excluded_intervals'], "independent_kpi_reconciliation":True,
                'only_allocation_caption_and_distribution_title_changed':True, 'spy_allocation_caption':'S&P500',
                'distribution_title':'MC Annualized Return Distribution - After Interest',
                'interest_basis':'Owner-confirmed interest already reflected in NAV; no additional charge',
                'independent_nav_after_interest_bootstrap':True,
                'simulation_paths':1000,'simulation_horizon_sessions':252,
                'factsheet_benchmark_label':'HIBOR + Spread',
                'chart_calculations_and_source_values_unchanged':True,
                'chart_vectors_identical_to_previous_version':True, "pages": pages}

    with ThreadPoolExecutor(max_workers=4) as pool:
        checks = list(pool.map(check, manifest["artifacts"]))
    record = {"status": "pass", "count": len(checks), "kpi_comparisons": len(checks) * 8,
        "raster_pages_verified": len(checks) * 2, "pixel_identical_to_reviewed_v14": True,
        "image_digest": manifest["image_digest"], "layout_version": manifest["layout_version"],
        "financial_methodology_independently_certified": False, "checks": checks}
    from deploy_alpha_world_ms_report_runtime import build
    build.save("pdf-verification", record)
    print(json.dumps({k: v for k, v in record.items() if k != "checks"}))


if __name__ == "__main__":
    main()
