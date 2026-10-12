"""Read-only independent arithmetic and PDF-vector audit of the 21 Azure PDFs.

Does not invoke or change the renderer, inputs, database, approvals or Azure.
Diagnostic JSON and visual contact sheets are new local audit evidence only.
"""
from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys

import numpy as np
from PIL import Image, ImageDraw
from pypdf import PdfReader
from pypdf.generic import ContentStream
from reportlab.pdfbase.pdfmetrics import stringWidth

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ibkr_etl.market_calendar import CALENDARS

FOLDER = ROOT / "output/pdf/AlphaWorld_MS_Azure_Factsheets_v13"
CAPTURE = ROOT / ".work/alpha-ms-factsheets-v13-20261007"
QA = ROOT / "tmp/pdfs/alpha-ms-graph-audit-20261007"
PLOTS = {"capital": (69., 367., 220., 95.), "rebased": (337., 367., 219., 95.),
         "distribution": (114., 106., 425., 125.)}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pdf_elements(page, reader):
    state = {"stroke": (0., 0., 0.), "fill": (0., 0., 0.), "width": 1., "dash": ((), 0.)}
    stack, paths, rectangles, pending, rect = [], [], [], [], None
    for args, op in ContentStream(page.get_contents(), reader).operations:
        if op == b"q":
            stack.append(dict(state))
        elif op == b"Q":
            state = stack.pop()
        elif op in (b"RG", b"rg"):
            state["stroke" if op == b"RG" else "fill"] = tuple(map(float, args))
        elif op == b"w":
            state["width"] = float(args[0])
        elif op == b"d":
            state["dash"] = (tuple(map(float, args[0])), float(args[1]))
        elif op == b"m":
            pending = [tuple(map(float, args))]
        elif op == b"l":
            pending.append(tuple(map(float, args)))
        elif op == b"re":
            rect = tuple(map(float, args))
        elif op == b"S":
            paths.append({**state, "points": pending})
            pending, rect = [], None
        elif op in (b"f", b"f*", b"B", b"B*", b"n"):
            if rect is not None and op != b"n":
                rectangles.append({**state, "rect": rect})
            pending, rect = [], None
    text = []
    def visitor(value, cm, tm, font, size):
        value = value.strip()
        if value:
            text.append({"text": value, "x": tm[4]*cm[0] + tm[5]*cm[2] + cm[4],
                "y": tm[4]*cm[1] + tm[5]*cm[3] + cm[5], "size": float(size)})
    page.extract_text(visitor_text=visitor)
    return paths, rectangles, text


def clean_step(low, high, ticks):
    rough = (high-low) / (ticks-1)
    magnitude = 10 ** math.floor(math.log10(rough))
    fraction = rough/magnitude
    step = next(v for v in (1, 2, 5, 10) if v >= fraction) * magnitude
    return step


def line_axis(values):
    low, high = min(values), max(values)
    padding = (high-low)*.05 if high != low else max(abs(low)*.05, 1.)
    return low-padding, high+padding


def check_close(actual, expected, tolerance=0.00012):
    if abs(actual-expected) > tolerance:
        raise AssertionError((actual, expected))


def risk_metrics(values, days, annual_rate):
    returns = np.array(values[1:])/np.array(values[:-1])-1
    growth = float(np.prod(1+returns))
    sigma = float(np.std(returns, ddof=1))
    threshold = float(np.quantile(returns, .05))
    wealth = np.cumprod(np.r_[1., 1+returns])
    return {"total_return": growth-1., "annualized_return": growth ** (365.25/days)-1.,
        "annualized_volatility": sigma * math.sqrt(252),
        "sharpe_ratio": (float(np.mean(returns))-((1+annual_rate)**(1/252)-1))/sigma*math.sqrt(252),
        "maximum_drawdown": float(np.min(wealth/np.maximum.accumulate(wealth)-1)),
        "var_95_daily": max(0., -threshold),
        "cvar_95_daily": max(0., -float(np.mean(returns[returns<=threshold]))),
        "win_rate": float(np.mean(returns>0))}


def audit(entry):
    period = entry["period_end"]
    path = FOLDER / Path(entry["blob_name"]).name
    original = path.read_bytes()
    snapshot_bytes = (CAPTURE / "snapshots" / (period + ".json")).read_bytes()
    assert sha(original) == entry["sha256"] and sha(snapshot_bytes) == entry["data_sha256"]
    snapshot = json.loads(snapshot_bytes)
    reader = PdfReader(path)
    assert len(reader.pages) == 2
    paths, rects, labels = pdf_elements(reader.pages[0], reader)
    issues = []
    rows = snapshot["performance"]["series"]
    dates = [date.fromisoformat(r["date"]) for r in rows]
    nav = [float(r["nav"]) for r in rows]
    assert all(v>0 and math.isfinite(v) for v in nav) and all(a<b for a,b in zip(dates,dates[1:]))
    line_paths = [p for p in paths if len(p["points"]) == len(rows) and abs(p["width"]-.9)<1e-6]
    assert len(line_paths) == 2
    line_checks = []
    for name, graph in zip(("capital", "rebased"), line_paths):
        left, bottom, width, height = PLOTS[name]
        values = [v/nav[0]*(100000 if name == "capital" else 100) for v in nav]
        low, high = line_axis(values)
        error = 0.
        for actual, day, value in zip(graph["points"], dates, values):
            expected = (left+width*(day-dates[0]).days/max(1,(dates[-1]-dates[0]).days),
                        bottom+height*(value-low)/(high-low))
            error = max(error, abs(actual[0]-expected[0]), abs(actual[1]-expected[1]))
            check_close(actual[0],expected[0]);check_close(actual[1],expected[1])
        tick_labels = [r for r in labels if left-70 < r["x"] < left and bottom-2 <= r["y"] < bottom+height+8 and r["size"] == 4.1]
        overflow = []
        ambiguous = []
        for label in tick_labels:
            text = label["text"].removeprefix("USD ").replace(",", "")
            try:
                value = float(text)
            except ValueError:
                continue
            actual_tick = low+(label["y"]+1.4-bottom)*(high-low)/height
            if abs(actual_tick-value)>max(1e-5,(high-low)*1e-6):
                ambiguous.append({"label":label["text"],"axis_value":actual_tick})
            if actual_tick>high+1e-5 or actual_tick<low-1e-5:
                overflow.append(label)
        if ambiguous:
            issues.append({"type":"rounded_axis_labels_misrepresent_ticks","graph":name,"ticks":ambiguous})
        if overflow:
            issues.append({"type": "axis_tick_outside_plot", "graph": name, "ticks": overflow})
        date_labels = [r for r in labels if abs(r["y"]-(bottom-6))<1e-5 and left-5<=r["x"]<=left+width+2
                       and len(r["text"])==7 and r["text"][4]=="-"]
        for label in date_labels:
            day = date.fromisoformat(label["text"]+"-01")
            check_close(label["x"]+stringWidth(label["text"],"Helvetica",label["size"])/2,left+width*(day-dates[0]).days/max(1,(dates[-1]-dates[0]).days))
        if not date_labels:
            issues.append({"type": "no_dated_x_ticks", "graph": name})
        line_checks.append({"graph": name, "plotted_points": len(values), "maximum_coordinate_error_pt": error,
                            "start_value": values[0], "end_value": values[-1], "date_ticks": len(date_labels)})
    assert "Initial USD 100,000" in reader.pages[0].extract_text() and "Base = 100" in reader.pages[0].extract_text()

    # Recompute the seeded return sample without calling any renderer helper.
    returns = [b/a-1. for a,b in zip(nav,nav[1:])]
    horizon = min(252,max(20,len(returns)))
    seed = int(hashlib.sha256(f"alpha-world-ms|{period}|alpha-world-distribution-v1".encode()).hexdigest()[:16],16)
    rng = random.Random(seed)
    simulated = []
    for _ in range(1000):
        wealth = 1.
        for _ in range(horizon):
            wealth *= 1+returns[rng.randrange(len(returns))]
        simulated.append(wealth**(252/horizon)-1)
    mean = statistics.fmean(simulated)
    sigma = statistics.stdev(simulated)
    skew = statistics.fmean(((x-mean)/sigma)**3 for x in simulated)
    hist = snapshot["risk"]["metrics"]["annualized_return"]
    ordered = sorted(simulated)
    low, high = max(-1.,ordered[10]), min(1.,ordered[990])
    if -1<=hist<=1:
        low, high = min(low,hist),max(high,hist)
    if high<=low:
        high=low+.01
    step = clean_step(low,high,7)
    low,high = max(-1.,math.floor(low/step)*step),min(1.,math.ceil(high/step)*step)
    if high<=low:
        high=low+step
    width_value=(high-low)/40
    counts = [0]*40
    for value in simulated:
        bounded = max(low,min(high,value))
        counts[min(39,int((bounded-low)/(high-low)*40))] += 1
    densities = [n/(1000*width_value) for n in counts]
    # Recover skew-normal location/scale independently from sample moments.
    delta_low, delta_high = 0., .995
    for _ in range(64):
        delta=(delta_low+delta_high)/2
        shift=delta*math.sqrt(2/math.pi)
        skew_target=(4-math.pi)/2*shift**3/(1-shift**2)**1.5
        if skew_target<abs(skew): delta_low=delta
        else: delta_high=delta
    delta=math.copysign((delta_low+delta_high)/2,skew)
    scale=sigma/math.sqrt(1-2*delta**2/math.pi)
    loc=mean-scale*delta*math.sqrt(2/math.pi)
    shape=delta/math.sqrt(1-delta**2)
    normal=lambda x: math.exp(-.5*((x-mean)/sigma)**2)/(sigma*math.sqrt(2*math.pi))
    fitted=lambda x: math.exp(-.5*((x-loc)/scale)**2)*(1+math.erf(shape*(x-loc)/scale/math.sqrt(2)))/(scale*math.sqrt(2*math.pi))
    grid=[low+(high-low)*i/120 for i in range(121)]
    density_high=max(max(densities),*(max(normal(x),fitted(x)) for x in grid))*1.05
    curve_paths=[p for p in paths if len(p["points"])==121 and abs(p["width"]-.65)<1e-6]
    assert len(curve_paths)==2
    left,bottom,width,height=PLOTS["distribution"]
    for curve, fn in zip(curve_paths,(fitted,normal)):
        for actual,x in zip(curve["points"],grid):
            check_close(actual[0],left+width*(x-low)/(high-low))
            check_close(actual[1],bottom+height*min(1.,fn(x)/density_high))
    assert curve_paths[0]["stroke"]==(0.,0.,0.) and curve_paths[0]["dash"][0]==()
    assert all(abs(v-115/255)<1e-5 for v in curve_paths[1]["stroke"]) and curve_paths[1]["dash"][0]==(2.,1.)
    bars=[r["rect"] for r in rects if abs(r["rect"][1]-bottom)<1e-5 and left<=r["rect"][0]<=left+width
          and abs(r["rect"][2]-(width/40-.4))<1e-5]
    assert len(bars)==40
    for i,(actual,density) in enumerate(zip(bars,densities)):
        check_close(actual[0],left+i*width/40+.2)
        check_close(actual[3],height*density/density_high)
    # Verify both vertical markers and the exact legend numbers/styles.
    for value,color,dash,label in ((mean,(.4,.4,.4),(2.,1.),f"MC Mean: {mean:.1%}"),
                                  (hist,(18/255,25/255,37/255),(.5,1.2),f"Historical: {hist:.1%}")):
        assert any(r["text"]==label for r in labels)
        matching=[p for p in paths if len(p["points"])==2 and abs(p["width"]-.7)<1e-6
                  and all(abs(a-b)<1e-5 for a,b in zip(p["stroke"],color))
                  and p["dash"][0]==dash and abs(p["points"][0][1]-bottom)<1e-5
                  and abs(p["points"][1][1]-(bottom+height))<1e-5]
        if low<=value<=high:
            assert len(matching)==1
            check_close(matching[0]["points"][0][0],left+width*(value-low)/(high-low))
        else:
            issues.append({"type":"legend_marker_outside_display_window", "legend":label})
    assert any(r["text"]=="1000 MC paths" for r in labels)
    assert abs(sum(densities)*width_value-1)<1e-12
    folded_low=sum(x<low for x in simulated)
    folded_high=sum(x>high for x in simulated)
    if folded_low+folded_high:
        issues.append({"type":"undisclosed_tail_folding", "low_paths":folded_low,"high_paths":folded_high,
                       "fraction":(folded_low+folded_high)/1000})
    density_ticks=[r for r in labels if left-10<r["x"]<left and bottom-2<=r["y"]<bottom+height+10 and r["size"]==4.2]
    for tick in density_ticks:
        value=float(tick["text"])
        actual_tick=(tick["y"]+1.4-bottom)*density_high/height
        if abs(actual_tick-value)>max(1e-5,density_high*1e-6):
            issues.append({"type":"rounded_axis_labels_misrepresent_ticks","graph":"distribution","label":value,"axis_value":actual_tick})
        if actual_tick>density_high+1e-5:
            issues.append({"type":"axis_tick_outside_plot","graph":"distribution","tick":value})
    distribution_x_ticks=[r for r in labels if r["size"]==4.2 and abs(r["y"]-(bottom-6))<1e-5 and r["text"].endswith("%")]
    assert distribution_x_ticks
    for tick in distribution_x_ticks:
        value=float(tick["text"].removesuffix("%"))/100
        check_close(tick["x"]+stringWidth(tick["text"],"Helvetica",tick["size"])/2,left+width*(value-low)/(high-low))
    # Each legend's swatch must actually represent the drawn series' style.
    legend_x,legend_top=left+width-62,bottom+height-2
    for i,color,dash in ((1,(0.,0.,0.),()),(2,(115/255,)*3,(2.,1.)),
                         (3,(.4,)*3,(2.,1.)),(4,(18/255,25/255,37/255),(.5,1.2))):
        y=legend_top-4-i*7
        assert any(len(p["points"])==2 and p["dash"][0]==dash
                   and all(abs(a-b)<1e-5 for a,b in zip(p["stroke"],color))
                   and abs(p["points"][0][0]-(legend_x+4))<1e-5 and abs(p["points"][0][1]-y)<1e-5
                   and abs(p["points"][1][0]-(legend_x+12))<1e-5 for p in paths)
    if horizon<252:
        issues.append({"type":"short_horizon_extrapolation","sampled_sessions":horizon,"annualization_exponent":252/horizon})
    fitted_skew=(4-math.pi)/2*(delta*math.sqrt(2/math.pi))**3/(1-2*delta**2/math.pi)**1.5
    if abs(fitted_skew-skew)>.01:
        issues.append({"type":"skew_fit_saturation","sample_skew":skew,"fitted_skew":fitted_skew})

    comparison=snapshot["performance_comparison"]["series"]
    risk=snapshot["risk"]["metrics"]
    rf=snapshot["risk"]["risk_free"]
    comp_dates=[date.fromisoformat(r["date"]) for r in comparison]
    nav_by_date=dict(zip(dates,nav))
    for row,day in zip(comparison,comp_dates):
        check_close(float(row["portfolio"]),nav_by_date[day]/nav_by_date[comp_dates[0]]*100,1e-10)
    calculated=risk_metrics([r["portfolio"] for r in comparison],(comp_dates[-1]-comp_dates[0]).days,rf["annual_rate"])
    differences={k:calculated[k]-risk[k] for k in calculated}
    if any(abs(v)>1e-10 for v in differences.values()):
        issues.append({"type":"risk_metric_recalculation_difference","differences":differences})
    kpi_fields=("annualized_return","annualized_volatility","sharpe_ratio","maximum_drawdown",
                "var_95_daily","cvar_95_daily","win_rate","total_return")
    for i,key in enumerate(kpi_fields):
        value=calculated[key]
        expected=f"{value:.2f}" if key=="sharpe_ratio" else f"{-abs(value):+.2%}" if key in {"var_95_daily","cvar_95_daily"} else f"{value:.2%}" if key=="win_rate" else f"{value:+.2%}"
        candidates=[r for r in labels if r["size"]==7 and abs(r["y"]-(650.5-15*i))<1e-5]
        assert len(candidates)==1 and candidates[0]["text"]==expected,(period,key,candidates,expected)
    graph_total=nav[-1]/nav[0]-1
    graph_cagr=(nav[-1]/nav[0])**(365.25/(dates[-1]-dates[0]).days)-1
    if abs(graph_total-risk["total_return"])>.000001:
        issues.append({"type":"graph_kpi_baseline_mismatch","graph_start":str(dates[0]),"kpi_start":risk["period_start"],
            "graph_total_return":graph_total,"kpi_total_return":risk["total_return"],
            "graph_calendar_cagr":graph_cagr,"historical_marker":hist})
    if risk["return_observations"] != len(returns):
        issues.append({"type":"simulation_risk_sample_mismatch","simulation_return_intervals":len(returns),
                       "risk_return_intervals":risk["return_observations"]})
    gaps=[]
    for before,after in zip(dates,dates[1:]):
        cursor=before+timedelta(days=1)
        omitted=[]
        while cursor<after:
            if CALENDARS["XNYS"].is_session(cursor): omitted.append(str(cursor))
            cursor+=timedelta(days=1)
        if omitted:
            gaps.append({"from":str(before),"to":str(after),"omitted_XNYS_sessions":omitted})
    if gaps:
        issues.append({"type":"multisession_returns_sampled_as_daily","intervals":len(gaps),"examples":gaps[-3:]})
    if snapshot["data_as_of"] != period:
        issues.append({"type":"report_date_differs_from_last_valuation","last_valuation":snapshot["data_as_of"]})
    total_from_sleeves=math.fsum(float(h["market_value_text"]) for h in snapshot["holdings"]["holdings"])
    check_close(total_from_sleeves,nav[-1],1e-10)
    weights={h["conid"]:float(h["market_value_text"])/nav[-1] for h in snapshot["holdings"]["holdings"]}
    page_two_text=reader.pages[1].extract_text()
    compact_text="".join(page_two_text.split())
    assert all(label.replace(" ","") in compact_text for label in ("70%","30%","91%","-49%","45%","-45%","130% LONG","70% SHORT"))
    # Do not confuse the source's latest risk-free yield with a historical mean.
    assert abs(risk["risk_free_annual_rate"]-rf["annual_rate"])<1e-12
    issues.extend([{"type":"risk_free_caption_not_period_average","rate":rf["annual_rate"],"rate_date":rf["rate_date"]},
                   {"type":"interest_model_not_explicit","constant_cash":58,"constant_cash_interest":0},
                   {"type":"static_allocation_is_inception_not_current","current_nav_weights":weights}])
    issues.append({"type":"allocation_short_sleeve_proxy_not_identified","displayed":"S&P500","calculation_input":"SPY unadjusted settlement price"})
    if "HIBOR + Spread" in reader.pages[0].extract_text():
        issues.append({"type":"template_benchmark_context_needs_confirmation","displayed":"HIBOR + Spread",
                       "configured_primary":snapshot["benchmark"]["name"],"risk_free_series":rf["series_id"]})
    if min(r["size"] for r in labels if r["text"] in {"Annualized Return","Density","Date"})<5:
        issues.append({"type":"small_axis_and_legend_font","axis_size_pt":4.0,"legend_size_pt":4.5})
    assert sha(path.read_bytes())==sha(original) and sha((CAPTURE/"snapshots"/(period+".json")).read_bytes())==sha(snapshot_bytes)
    return {"period_end":period,"pdf_sha256":entry["sha256"],"source_sha256":entry["data_sha256"],
        "vector_arithmetic_pass":True,"legend_style_pass":True,"distribution_x_axis_pass":True,"rendered_kpi_values_checked":8,"line_charts":line_checks,"risk_recalculation_max_error":max(abs(v) for v in differences.values()),
        "graph_total_return":graph_total,"graph_calendar_cagr":graph_cagr,"kpi_total_return":risk["total_return"],
        "historical_marker":hist,"distribution":{"paths":1000,"horizon_sessions":horizon,"mc_mean":mean,
            "mc_mean_standard_error":sigma/math.sqrt(1000),"display_minimum":low,"display_maximum":high,
            "tail_folded_paths":folded_low+folded_high,"density_area":sum(densities)*width_value,
            "sample_skew":skew,"fitted_skew":fitted_skew,"maximum_density_axis":density_high},
        "issues":issues}


def contacts(periods):
    source=ROOT/"tmp/pdfs/alpha-ms-azure-v13"
    # Four large chart crops per sheet retain label and axis legibility.
    for graph,box in (("performance",(45,588,960,842)),("distribution",(120,960,950,1270)),
                      ("allocation",(30,85,950,400))):
        for start in range(0,len(periods),4):
            sheet=Image.new("RGB",(1900,750 if graph=="performance" else 860),"#eeeeee")
            draw=ImageDraw.Draw(sheet)
            for index,period in enumerate(periods[start:start+4]):
                page=2 if graph=="allocation" else 1
                image=Image.open(source/f"{period}-azure-{page}.png").convert("RGB").crop(box)
                image.thumbnail((925,370))
                x,y=index%2*950+10,index//2*(375 if graph=="performance" else 430)+25
                sheet.paste(image,(x,y));draw.text((x,y-18),period,fill="black")
            sheet.save(QA/f"{graph}-contact-{start//4+1}.png")


def main():
    QA.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((FOLDER/"manifest.json").read_bytes())
    checks=[audit(entry) for entry in manifest["artifacts"]]
    counts={}
    for check in checks:
        for kind in {issue["type"] for issue in check["issues"]}:
            counts[kind]=counts.get(kind,0)+1
    record={"status":"review_findings_require_resolution","pdfs":len(checks),"line_graphs":2*len(checks),
        "distribution_graphs":len(checks),"allocation_diagrams":len(checks),"vector_arithmetic_pass":True,
        "source_data_changed":False,"pdfs_changed":False,"azure_changed":False,
        "scope_limit":"Independent arithmetic from frozen inputs, not a complete raw-IBKR/FX/sleeve lineage reconciliation",
        "issue_counts_by_factsheet":counts,"checks":checks}
    target=ROOT/".work/alpha-ms-azure-v13-20261007/graph-review-validated.json"
    with target.open("x",encoding="utf-8") as handle:json.dump(record,handle,indent=2)
    contacts([r["period_end"] for r in checks])
    print(json.dumps({"pdfs":len(checks),"issue_counts":counts,
        "august":next(r for r in checks if r["period_end"]=="2026-08-31"),"audit":str(target)},indent=2))


if __name__=="__main__":
    main()
