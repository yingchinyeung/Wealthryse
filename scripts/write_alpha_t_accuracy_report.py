"""Produce the private audit report from immutable evidence, without DB writes."""
import hashlib,html,json
from pathlib import Path
from audit_alpha_t_private_presentation import audit

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'.work/alpha-world-t-accuracy-audit-20261009'
OUT=ROOT/'output/audit/AlphaWorld_T_Accuracy_20261009'
DECK=ROOT/'output/pptx/AlphaWorld_T_Private_Audit_20261009/AlphaWorld_T_Presentation_2026-09-30_Private_v2.pptx'

def main():
    r=json.loads((WORK/'independent-reconciliation.json').read_bytes())
    p=audit(DECK,WORK/'presentation-input-2026-09-30.json')
    OUT.mkdir(parents=True,exist_ok=True)
    metrics=r['monthly'][-1]['metrics']
    esc=html.escape
    def table(headers,rows):
        return '<table><thead><tr>'+''.join('<th>'+esc(str(x))+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table>'
    summary=table(['Area','Result','Scope / limitation'],[
      ['Live Azure T registration','Not populated','0 T NAV rows, reporting snapshots or generation runs. This is not a production T certification.'],
      ['Local T NAV / components','Reconciled','448 NAV observations and 2,240 component rows through 7 October 2026.'],
      ['Underlying accounts','Arithmetic reconciled','896 NAVs, 32,585 positions, 892 cash summaries. Original broker correctness is still unverified.'],
      ['Existing factsheets','21 quantitative checks passed','January 2025 to September 2026. 168 printed KPIs plus 21 HIBOR footnotes.'],
      ['Factsheet simulations','21,000 paths reproduced','1,000 paths per month, each with 252 sampled returns. Model output, not a forecast guarantee.'],
      ['New private presentation','September 2026 only','20 slides, 6 editable charts, 7 risk metrics, 5 component values and 21 monthly returns verified.'],
      ['All-month presentations','Not yet generated','No existing T presentations were registered. One private September deck was created using the approved 20-slide template.']])
    findings=table(['Priority','Finding','Meaning / resolution'],[
      ['High','Original broker provenance incomplete','The two source accounts were imported from an analyst SQLite snapshot on 8 October. No raw broker report is linked to those ingestion runs. Import PASS confirms row transfer, not independent accuracy. Reconcile original IBKR statements/Flex reports, including cash flows, corporate actions and trades.'],
      ['High','Cash records incomplete','Cash snapshots are absent for both accounts on 27 and 28 August 2026 (four account/date gaps). All 2,674 cash transaction records lack transaction type and ID. Retrieve original records before interpreting external flows or performing transaction-level attribution.'],
      ['Review','452 repeated economic-key groups','Same account/date/currency/amount/description under different source keys. These are possible duplicates, not proven errors. Do not delete or alter NAV without broker evidence.'],
      ['High','Narrative and model differ','Factsheet wording retained at the owner\'s request describes dynamic rotation and leverage generally below 2x. The implemented model uses fixed units, 230% initial gross risky exposure and 208.35% at September month-end. Resolve the intended definition and disclosure before publication; no wording was changed in this audit.'],
      ['Review','Allocation percentages lack an inception label','91%, 45%, -49% and -45% are initial coefficients, not current portfolio weights. September signed weights are listed below. Clarify the diagram basis before release.'],
      ['Open','Publisher cross-check incomplete','Stored benchmark/FX/HIBOR data passed internal conversion, date and arithmetic checks. The official HKMA request timed out. This audit did not independently verify every fixing, price or FX rate with its publisher.'],
      ['Open','Presentation unavailable analytics','T has no qualified benchmark-relative statistics, crisis replay, BLUE estimate or simulated drawdown analysis. N/A values in the private deck mean unavailable, not zero. The factsheet return-distribution bootstrap is a different analysis.'],
      ['Open','Template-specific legal / contact terms','T fee terms are not established. The template legal disclaimer remains supplied reference content, not a legal opinion or verified T issuance statement. Contact details need confirmation before distribution.'],
      ['Open','Native PowerPoint acceptance','Every final slide was rendered and visually inspected with Artifact Tool. Package, geometry, native chart and embedded-workbook checks passed. Native PowerPoint editing/save/reopen was not tested this turn.']])
    kpis=table(['September 2026 metric','Value'],[(k.replace('_',' '),f'{v:.8f}' if k=='sharpe_ratio' else f'{v*100:.8f}%') for k,v in metrics.items()])
    weights=table(['Component','Signed value / current NAV'],[(k,f'{v*100:.6f}%') for k,v in r['september_current_exposure_weights'].items()])
    months=table(['Factsheet date','Valuation date','NAV observations','Total return','Annualised return','Volatility','Sharpe','Short history','Check'],[
      [x['period_end'],x['data_as_of'],x['nav_observations'],f"{x['metrics']['total_return']:.4%}",f"{x['metrics']['annualized_return']:.4%}",
       f"{x['metrics']['annualized_volatility']:.4%}",f"{x['metrics']['sharpe_ratio']:.6f}",'Yes' if x['short_history'] else 'No','Pass'] for x in r['monthly']])
    files=[WORK/'live-inputs.json',WORK/'database-audit-result.json',WORK/'provenance.json',WORK/'independent-reconciliation.json',WORK/'official-hibor-check.json',DECK]
    evidence=table(['Evidence file','SHA-256'],[(str(x.relative_to(ROOT)),hashlib.sha256(x.read_bytes()).hexdigest()) for x in files])
    body=f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Alpha World T accuracy review, 9 October 2026</title>
<style>body{{font:16px/1.55 Arial,sans-serif;max-width:1180px;margin:40px auto;padding:0 28px;color:#172533}}h1,h2{{color:#123d35}}table{{border-collapse:collapse;width:100%;margin:20px 0 30px;font-size:14px}}th,td{{border:1px solid #cbd4d8;padding:9px;text-align:left;vertical-align:top;overflow-wrap:anywhere}}th{{background:#e9f1ef}}.verdict{{border-left:5px solid #ad7525;padding:12px 20px;background:#fff6e8}}code{{white-space:normal}}@media print{{body{{margin:0;font-size:11px}}table{{font-size:10px}}}}</style>
<h1>Alpha World T: calculation and report accuracy review</h1><p>9 October 2026. Private review. Azure source snapshot captured at {esc(r['captured_at'])}.</p>
<div class="verdict"><strong>Arithmetic reconciles to the stored inputs, but complete data correctness and publication readiness are not established.</strong> Original broker evidence, transaction completeness, provider confirmation and narrative consistency remain open. No live financial data, approvals, factsheets or Azure service settings were changed.</div>
<h2>1. Scope and results</h2>{summary}
<p>The live database was inspected inside repeatable-read, read-only transactions followed by rollback. Protected audit evidence was exported to private storage; production source tables were not modified. The local qualification SQLite database was opened read-only. All 21 original PDF and snapshot hashes remained unchanged.</p>
<h2>2. Findings requiring attention</h2>{findings}
<h2>3. Independently checked calculation method</h2>
<p><code>NAV(t) = 58 + 91 × GC_HKD(t)/GC_HKD(0) + 45 × US_HKD(t)/US_HKD(0) − 49 × HSI(t)/HSI(0) − 45 × SPY_HKD(t)/SPY_HKD(0).</code></p>
<p>Initial NAV is 100 on 20 January 2025. Greater China includes Taiwan and the original account cash. USD account NAV and SPY prices are converted into HKD before normalization. HSI uses a price index; SPY uses unadjusted price history. Fixed units allow exposures to drift. The constant cash amount stays at 58 and earns no added interest. Account NAV already contains its interest effects.</p>
<p>Independent Decimal reconstruction agrees with all 448 NAVs within {r['maximum_nav_error']} NAV units and component fields within {r['maximum_component_error']}. Both account NAV component sums and the converted holdings-to-NAV identities match exactly. Position quantity × mark-price differences are at most 0.005 currency units, within the 0.02 rounding tolerance. The maximum cash-summary difference is zero. These are consistency checks, not independent market-price certification.</p>
<p>Total return uses last NAV / first NAV − 1. CAGR uses 365.25 calendar days/year. Volatility uses sample daily standard deviation × √252. Sharpe uses mean daily return minus ((1 + average HIBOR)<sup>1/252</sup> − 1), divided by sample standard deviation, then multiplied by √252. Maximum drawdown uses running peaks. Daily VaR is the non-negative loss corresponding to the interpolated fifth return percentile. CVaR is the mean loss at or below that return threshold. Win rate counts strictly positive returns. HIBOR is the arithmetic average of published stored 1-month fixings from inception through valuation, not the reporting-month average.</p>
<p>US, Hong Kong, Shanghai and Taiwan calendar checks were applied. No return interval was excluded for a missing session. These calendars share the governed platform reference, so their independent external correctness was not recertified. Early-month annualisation and bootstrap estimates use limited history and are marked accordingly.</p>
<h2>4. September reconciliation</h2>{kpis}{weights}
<p>September has 443 NAV observations and 442 daily returns. Gross risky exposure is {r['september_gross_risky_exposure']:.6%}; cash is excluded from this measure. Presentation concentration charts instead divide absolute component values, including cash, by their absolute sum. Those gross-composition percentages are a different denominator, not a discrepancy.</p>
<h2>5. Monthly factsheet coverage</h2>{months}
<p>PDF validation checked every plotted NAV observation, date/axis mapping, labels, allocation branch geometry, 40-bin histograms (840 bins in total), fitted normal and skew-normal curves (121 evaluation points per curve), density normalization and no folded tails. Independent deterministic bootstrap reconstruction matched every one of the 21,000 paths. All 42 pages were rendered; all 21 monthly chart regions were visually inspected. This is not a claim that every full PDF page received a fresh standalone visual review.</p>
<h2>6. Private 20-slide presentation</h2><p>The September deck uses the approved 20-slide template with actual T inputs, not MS performance. Its six editable charts and their workbook caches match the source categories and values. Seven risk-table metrics, five signed component values and all 21 monthly return cells match the audited data. Fixed-unit monthly contribution equals component change divided by previous month-end NAV; effects reconcile to the monthly return. Printed VaR/CVaR are positive loss magnitudes, while the factsheet uses negative return signs.</p>
<p>Every slide was visually reviewed. The final cover and two other changed slides were rechecked; the remaining 17 rendered slides are byte-identical to the reviewed draft. Template backgrounds extending slightly beyond the canvas are retained intentionally. The final deck is private, contains explicit evidence limitations and is not registered or approved on Azure. No other monthly T presentations were created in this review.</p>
<h2>7. Recommended next actions</h2><ol><li>Obtain original IBKR records for both source accounts and reconcile every NAV, transaction, cash flow and corporate action. Resolve missing cash dates and possible duplicates without speculative deletion.</li><li>Confirm stored benchmark prices, FX and 1-month HIBOR against licensed provider evidence.</li><li>Resolve the factsheet narrative and explicitly distinguish initial allocation from current exposure. Existing wording remains untouched pending the owner’s decision.</li><li>Qualify the missing T benchmark/crisis analytics or agree their explicit exclusion, review template legal/contact content, then render and review the remaining monthly presentations.</li><li>Only after those checks, qualify an Azure release and seek separate report approval/onboarding acceptance.</li></ol>
<h2>8. Evidence manifest</h2>{evidence}<p>Audit scripts and private evidence reside under the project’s scripts and .work/alpha-world-t-accuracy-audit-20261009 directories. This report does not certify legal compliance or future investment outcomes.</p></html>'''
    with (OUT/'AlphaWorld_T_Accuracy_Review.html').open('x',encoding='utf-8') as f:f.write(body)
    result={'database_and_factsheets':r,'presentation':p,'final_slide_review':{'count':20,'changed_rechecked':[1,17,20],'unchanged_renders':17},'production_mutated':False}
    with (OUT/'AlphaWorld_T_Accuracy_Evidence.json').open('x',encoding='utf-8') as f:json.dump(result,f,indent=2,allow_nan=False)
    print(OUT/'AlphaWorld_T_Accuracy_Review.html')
if __name__=='__main__':main()
