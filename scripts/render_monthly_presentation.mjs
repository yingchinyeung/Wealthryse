import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { FileBlob, PresentationFile } from "@oai/artifact-tool";
import { cashActivityNote, portfolioMonthlyNote, attributionResidualNote } from './report_content_labels.mjs';
import { genericSummaryLabel, boundedGenericCommentary } from './report_summary_label.mjs';

const [sourcePath, snapshotPath, finalPath, commentaryPath] = process.argv.slice(2);
if (!sourcePath || !snapshotPath || !finalPath) {
  throw new Error("Usage: render_monthly_presentation.mjs template.pptx snapshot.json output.pptx");
}
const snapshot = JSON.parse(await fs.readFile(snapshotPath, "utf8"));
const commentary = commentaryPath
  ? JSON.parse(await fs.readFile(commentaryPath, "utf8"))
  : null;
const isMsHkd = snapshot.subject_id === 'alpha-world-ms' && snapshot.reporting_currency === 'HKD';
if (!snapshot.subject_id || (snapshot.reporting_currency !== "USD" && !isMsHkd)) {
  throw new Error("Presentation renderer requires a subject id and USD reporting");
}
if (snapshot.data_as_of > snapshot.period_end) throw new Error("Snapshot data is future-dated");
if (!isMsHkd && (snapshot.sector_allocation?.currency !== snapshot.reporting_currency ||
    snapshot.holdings?.currency !== snapshot.reporting_currency ||
    !snapshot.holdings?.holdings?.length ||
    snapshot.holdings.holdings.some(row => row.currency !== snapshot.reporting_currency))) {
  throw new Error('Holdings and allocation must be normalized to the reporting currency before rendering');
}
const msModel = isMsHkd ? JSON.parse(execFileSync(process.env.RUNTIME_PYTHON || process.env.PYTHON_BINARY || 'python',
  ['-m','ibkr_etl.alpha_world_ms_presentation',snapshotPath], {encoding:'utf8'})) : null;
if (isMsHkd && msModel.version !== 'alpha-world-ms-presentation-hkd-hibor-v1') throw new Error('MS rendering model version differs');
// Portfolio returns come from the full cash-flow-adjusted NAV history, as on
// the factsheet. The benchmark comparison only covers dates both series share,
// so a benchmark-market holiday must not shorten the portfolio's own returns.
const portfolioModel = isMsHkd ? null : JSON.parse(execFileSync(process.env.RUNTIME_PYTHON || process.env.PYTHON_BINARY || 'python',
  ['-m','ibkr_etl.presentation_portfolio_returns',snapshotPath], {encoding:'utf8'}));
if (!isMsHkd && portfolioModel.version !== 'presentation-portfolio-returns-v1') throw new Error('Portfolio return model version differs');

const presentation = await PresentationFile.importPptx(await FileBlob.load(sourcePath));
const tableDimensions = isMsHkd ? new Map((await presentation.inspect({kind:'table',maxChars:1000000})).ndjson
  .split('\n').filter(Boolean).map(line=>{const item=JSON.parse(line);return [item.id,{rows:item.rows,cols:item.cols}];})) : null;
const fallbackStrategyNames = { U16088606: "iStrategy: Greater China" };
const governedStrategyName = String(
  process.env.PRESENTATION_STRATEGY_NAME || fallbackStrategyNames[snapshot.subject_id] || "",
).trim();
if (governedStrategyName && !governedStrategyName.startsWith("iStrategy:")) {
  throw new Error("The governed presentation strategy name is invalid");
}
const applyPresentationChartFont = (chart, options = {}) => {
  const family = options.fontFamily || "Arial";
  const styles = [chart.legend?.textStyle, chart.dataLabels?.textStyle,
    chart.xAxis?.textStyle, chart.yAxis?.textStyle];
  if (typeof chart.title === "string" && chart.title.trim()) styles.push(chart.titleTextStyle);
  for (const axis of [chart.xAxis, chart.yAxis]) {
    if (typeof axis?.title?.text === "string" && axis.title.text.trim()) styles.push(axis.title.textStyle);
  }
  for (const style of styles) if (style) style.typeface = family;
  return chart;
};
const percent = (value, digits = 2) => value == null ? "N/A" : `${(Number(value) * 100).toFixed(digits)}%`;
const points = value => value == null ? "N/A" : `${(Number(value) * 100).toFixed(2)} pp`;
const decimal = (value, digits = 2) => value == null ? "N/A" : Number(value).toFixed(digits);
const round = (value, digits = 6) => Number(Number(value).toFixed(digits));
const securityName = row => {
  const description = String(row?.description || "").trim().replace(/\s+/g, " ");
  return description || String(row?.symbol || "Security").trim() || "Security";
};
const normalizedAxis = (values, step = 10) => {
  const clean = values.map(Number).filter(Number.isFinite);
  if (!clean.length) throw new Error("Normalized performance chart has no finite values");
  const min = Math.floor(Math.min(...clean) / step) * step;
  const maxCandidate = Math.ceil(Math.max(...clean) / step) * step;
  if (maxCandidate > min) return { min, max: maxCandidate, majorUnit: step };
  return { min: min - step, max: maxCandidate + step, majorUnit: step };
};
const money = value => isMsHkd ? money2(value) : `$${Math.round(Number(value)).toLocaleString("en-US")}`;
const money2 = value => {
  if (isMsHkd && value == null) return 'N/A';
  const amount = Number(value || 0);
  const formatted = Math.abs(amount).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${amount < 0 ? "-" : ""}${isMsHkd ? 'HKD ' : '$'}${formatted}`;
};
const shortDate = value => {
  const [year, month, day] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })
    .format(new Date(Date.UTC(year, month - 1, day)));
};
const longDate = value => {
  const [year, month, day] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" })
    .format(new Date(Date.UTC(year, month - 1, day)));
};
const monthName = value => {
  const [year, month] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("en-GB", { month: "long", timeZone: "UTC" })
    .format(new Date(Date.UTC(year, month - 1, 1)));
};
const setText = (id, value) => presentation.resolve(id).text.set(String(value));
const setTable = (id, values) => {
  const table = presentation.resolve(id);
  if (isMsHkd) {
    // No reference-account values may survive in unused cells.
    const dimensions=tableDimensions.get(id);
    if (!dimensions || !Number.isInteger(dimensions.rows) || !Number.isInteger(dimensions.cols)) throw new Error('MS table dimensions unavailable');
    for (let row=0;row<dimensions.rows;row++) {
      for (let column=0;column<dimensions.cols;column++) table.cells.set(row,column,'');
    }
  }
  for (let row = 0; row < values.length; row += 1) {
    for (let column = 0; column < values[row].length; column += 1) {
      table.cells.set(row, column, String(values[row][column]));
    }
  }
};
const setTableCellFontSize = (id, row, column, fontSize) => {
  const table = presentation.resolve(id);
  table.cells.block({ row, column, rowCount: 1, columnCount: 1 })
    .textStyle.fontSize = fontSize;
};
const widenSummaryTable = id => {
  const table = presentation.resolve(id);
  table.position.width = 300;
  table.columns.get(0).width = 130;
  table.columns.get(1).width = 170;
};
const replaceChart = (slideId, chartId, chartType, config) => {
  const slide = presentation.resolve(slideId);
  const existing = presentation.resolve(chartId);
  slide.charts.deleteById(existing.id);
  if (isMsHkd && chartType==='bar' && config.barOptions?.direction==='bar') {
    // PowerPoint's category/value axes retain their semantic roles when bars
    // run horizontally. Numeric controls belong to yAxis in the export API.
    [config.xAxis,config.yAxis]=[config.yAxis,config.xAxis];
  }
  const chart = slide.charts.add(chartType, config);
  applyPresentationChartFont(chart, { fontFamily: "Arial" });
  return chart;
};

const primary = isMsHkd ? msModel.primary : snapshot.performance_comparison.metrics;
const secondary = isMsHkd ? msModel.secondary : snapshot.secondary_performance_comparison.metrics;
const subjectLabels = {
  U16088571: "U16088571 (U.S. Technology and Biotechnology)",
  U16088606: "U16088606-Greater China",
};
const governedDisplayName = governedStrategyName.replace(/^iStrategy:\s*/, "").trim();
const subjectLabel = governedDisplayName || subjectLabels[snapshot.subject_id] || snapshot.subject_id;
if (governedStrategyName) {
  setText("sh/nu58f2hs", governedDisplayName);
  for (const id of [
    "sh/pcjqtg36", "sh/p0batw72", "sh/032tgr6d", "sh/7m98ru9g", "sh/l0vuh0rm",
    "sh/sjix0zy1", "sh/ofq5svm5", "sh/kzu5c32t", "sh/o7ih0r6h", "sh/sb2lcnmd",
    "sh/32ponyts", "sh/kfidonqx", "sh/s7y5sv2x", "sh/lwbyxwra", "sh/snedsvml",
  ]) {
    setText(id, governedStrategyName);
  }
}
const primaryBenchmarkName = isMsHkd ? 'HIBOR + Spread (unavailable)' : snapshot.benchmark?.name || snapshot.benchmark?.benchmark_id || "Primary benchmark";
const secondaryBenchmarkName = isMsHkd ? 'Secondary benchmark (unavailable)' : snapshot.secondary_benchmark?.name || snapshot.secondary_benchmark?.benchmark_id || "Secondary benchmark";
if (!isMsHkd) {
  const labels = await presentation.inspect({kind:'textbox',maxChars:1000000});
  const replacements = new Map([
    ['EXCESS VS S&P 500', 'BENCHMARK EXCESS'],
    ['Primary and secondary benchmarks selectable in the staging website', 'Primary and secondary benchmark comparison'],
    ['Growth of 100 — portfolio versus S&P 500 Price Index', `Growth of 100 — portfolio versus ${primaryBenchmarkName}`],
  ]);
  for (const line of labels.ndjson.split('\n').filter(Boolean)) {
    const item=JSON.parse(line);
    const text=String(item.text || item.textPreview || '').trim();
    if (replacements.has(text)) setText(item.id,replacements.get(text));
  }
}
const risk = isMsHkd ? msModel.risk : snapshot.risk.metrics;
const crisis = snapshot.risk.crisis_replay;
const sector = snapshot.sector_allocation;
const isComposite = sector.classification_basis === "composite sleeve type";
const holdingNoun = isComposite ? "component" : "holding";
const allocationNoun = isComposite ? "component type" : "sector";
const monthlyReporting = snapshot.monthly_reporting;
if (!monthlyReporting?.available) {
  throw new Error("Snapshot does not contain validated monthly reporting analytics");
}
const periodEnd = snapshot.period_end;
const dataAsOf = snapshot.data_as_of;
const reportMonth = monthName(periodEnd);
const monthRows = isMsHkd ? msModel.month_rows : snapshot.month_end_performance;
const latestMonth = monthRows.at(-1) ?? {};
const portfolioMonthRows = isMsHkd ? monthRows : portfolioModel.month_rows;
if (!isMsHkd && portfolioMonthRows.at(-1)?.date !== dataAsOf) throw new Error('Portfolio month-end history does not end on data_as_of');
const monthReturn = portfolioMonthRows.at(-1)?.portfolio_monthly_return;
// The benchmark month only counts when the comparison history reaches this month.
const benchmarkMonthReturn = latestMonth.date?.slice(0, 7) === dataAsOf.slice(0, 7) ? latestMonth.benchmark_monthly_return : null;
const portfolioTotalReturn = isMsHkd ? primary.portfolio_total_return : portfolioModel.total_return;
const portfolioStart = isMsHkd ? primary.period_start : portfolioModel.period_start;
const holdings = [...snapshot.holdings.holdings]
  .sort((a, b) => Math.abs(Number(b.market_value_text)) - Math.abs(Number(a.market_value_text)));
const grossMarketValue = Number(sector.gross_market_value);
const nav = Number(snapshot.portfolio_summary.latest_nav.total_text);
const weights = holdings.map(row => Math.abs(Number(row.market_value_text)) / grossMarketValue);
const top5 = weights.slice(0, 5).reduce((sum, value) => sum + value, 0);
const top10 = weights.slice(0, 10).reduce((sum, value) => sum + value, 0);
const largestSector = [...sector.allocation].sort((a, b) => Number(b.weight) - Number(a.weight))[0];
const relativePhrase = isMsHkd ? 'has no qualified comparison with' : Number(primary.excess_total_return) >= 0 ? "leads" : "trails";
const monthPhrase = isMsHkd
  ? `${reportMonth} returned ${percent(monthReturn)}. No qualified benchmark comparison is available.`
  : monthReturn == null || benchmarkMonthReturn == null
  ? `${reportMonth} is the opening partial reporting month, so a comparable monthly return is not shown.`
  : `${reportMonth} ${Number(monthReturn) >= 0 ? "gained" : "fell"} ${percent(Math.abs(monthReturn))}, ${Number(monthReturn) >= Number(benchmarkMonthReturn) ? "ahead of" : "behind"} ${primaryBenchmarkName}'s ${percent(benchmarkMonthReturn)} for the month.`;

setText("sh/14ryd8fq", `Monthly portfolio review | ${subjectLabel} | ${longDate(periodEnd)}`);
setText("sh/sfix03y9", `${reportMonth.toUpperCase()} RETURN`);
setText("sh/tgry9ofu", percent(monthReturn));
setText("sh/a9kfadwn", `${primaryBenchmarkName}: ${percent(benchmarkMonthReturn)}`);
setText("sh/z2d07mh4", percent(portfolioTotalReturn));
setText("sh/y14jehgj", `Since ${shortDate(portfolioStart)}`);
setText("sh/ax4zax0n", points(primary.excess_total_return));
setText("sh/21szqxcb", decimal(risk.sharpe_ratio));
setText("sh/32l0j2dw", `Risk-free rate: ${percent(risk.risk_free_annual_rate)}`);
setTable("tb/fql4fa5s", [
  ["Performance", `${monthPhrase} Since ${shortDate(portfolioStart)}, the portfolio returned ${percent(portfolioTotalReturn)}.`],
  ["Relative result", isMsHkd ? 'Benchmark-relative results are unavailable in the qualified HKD history.' : `The portfolio ${relativePhrase} ${primaryBenchmarkName} by ${points(Math.abs(primary.excess_total_return))} cumulatively and differs from ${secondaryBenchmarkName} by ${points(Math.abs(secondary.excess_total_return))}.`],
  ["Portfolio shape", `${largestSector.sector} represents ${percent(largestSector.weight)} of gross market value. The five largest ${holdingNoun}s represent ${percent(top5)} of gross exposure.`],
  ["Risk", `Annualized volatility is ${percent(primary.portfolio_annualized_volatility)}, observed maximum drawdown is ${percent(primary.portfolio_max_drawdown)}, and daily expected tail loss is ${percent(risk.daily_expected_tail_loss)}.`],
]);

setText("sh/l8bi9sjq", `${subjectLabel} | Reporting currency: ${snapshot.reporting_currency} | ${longDate(periodEnd)}${isMsHkd ? ' | NAV per inception 100' : ''}`);
setText("sh/9cvidcjm", money(nav));
setText("sh/v2p0bqdw", percent(portfolioTotalReturn));
setText("sh/8ze10be5", isMsHkd ? `${primary.period_start} to ${primary.period_end}` : `${portfolioModel.period_start} to ${portfolioModel.period_end}`);
setText("sh/kvehwre9", percent(primary.portfolio_annualized_return));
setText("sh/lwni5wvu", `${primary.return_observations} daily returns`);
setText("sh/oru1ory1", percent(primary.portfolio_annualized_volatility));
setText("sh/jy5kn6ho", decimal(risk.sharpe_ratio));
setText("sh/yxwju1g3", `Risk-free rate ${percent(risk.risk_free_annual_rate)}`);
setText("sh/wv2l8rex", percent(primary.portfolio_max_drawdown));
setText("sh/z29kr2x4", percent(risk.daily_expected_tail_loss));
setText("sh/n650zydw", holdings.length);
setText("sh/03eho3ul", `${Number(sector.classification_coverage_pct).toFixed(0)}% ${isComposite ? "component coverage" : "sector coverage"}`);
setText("sh/14nyxovq", isMsHkd ? `HKD total return was ${percent(primary.portfolio_total_return)}. Volatility was ${percent(primary.portfolio_annualized_volatility)} and maximum drawdown was ${percent(primary.portfolio_max_drawdown)}. Benchmark-relative results are unavailable.` : `Portfolio return was ${percent(portfolioTotalReturn)} since ${shortDate(portfolioStart)}. Annualized volatility was ${percent(primary.portfolio_annualized_volatility)}, and the observed maximum drawdown was ${percent(primary.portfolio_max_drawdown)}. ${largestSector.sector} represented ${percent(largestSector.weight)} of gross market value.`);

setText("sh/ud0ne10b", `Top 10 ${isComposite ? "components by gross exposure" : "positions by gross market value"} | ${longDate(periodEnd)}`);
setText("sh/4ji5kbid", isMsHkd ? `Gross sleeve exposure: ${money(grossMarketValue)} per initial NAV 100. Signed values in HKD.` : `Gross ${isComposite ? "sleeve exposure" : "market value"}: ${money(grossMarketValue)}  •  ${holdings.length} ${holdingNoun}s  •  Values shown in USD`);
const holdingTable = [isMsHkd ? ['Rank','Sleeve','Component','Quantity','Signed value','% of gross'] : ["Rank", "Ticker", "Security", "Quantity", "Market value", "% of gross"]];
holdings.slice(0, 10).forEach((row, index) => holdingTable.push([
  String(index + 1), isMsHkd ? ({'hong-kong-china':'HK-China','U16088571':'US-Tech','hang-seng-price-index':'HSI','spy-settlement':'SPY','constant-cash':'Cash'}[row.conid] || row.symbol) : row.symbol,
  isMsHkd ? ({'hong-kong-china':'China & Hong Kong','U16088571':'US Tech & Biotech','hang-seng-price-index':'Hang Seng Index','spy-settlement':'S&P500 (SPY)','constant-cash':'Constant cash'}[row.conid] || row.description || row.symbol) : row.description || row.symbol,
  isMsHkd && row.quantity_text == null ? 'N/A' : Number(row.quantity_text).toLocaleString("en-US"),
  money(row.market_value_text), percent(Math.abs(Number(row.market_value_text)) / grossMarketValue),
]));
setTable("tb/gjutwb61", holdingTable);
if (isMsHkd) {
  // Native PowerPoint expands the retained blank rows beyond the declared frame.
  // Keep all rows and typography, but leave clear space below the actual table.
  presentation.resolve('sh/4ji5kbid').position.top += 24;
  // Keep the approved frame, rows and font sizes. Smaller vertical cell
  // padding prevents native PowerPoint's minimum row height reaching the note.
  const dimensions = tableDimensions.get('tb/gjutwb61');
  presentation.resolve('tb/gjutwb61').cells.block({row:0,column:0,
    rowCount:dimensions.rows,columnCount:dimensions.cols}).assign({
      margins:{left:9.6,right:9.6,top:2,bottom:2},
    });
}

setText("sh/bq547yhw", `Current holdings concentration | ${longDate(periodEnd)}`);
setText("sh/ydgnit07", `${securityName(holdings[0])} is the largest ${holdingNoun}. ${largestSector.sector} has the highest ${allocationNoun} weight.`);
replaceChart("sl/x8f69ofe", "ch/w7yloryp", "bar", {
  position: { left: 52.5, top: 142.5, width: 540, height: 292.5 },
  title: `Top 10 ${holdingNoun}s — % of gross exposure`,
  titleTextStyle: { typeface: "Arial", fontSize: 15, bold: true, fill: "#151b26" },
  categories: holdings.slice(0, 10).map(securityName),
  series: [{ name: "Weight", values: weights.slice(0, 10).map(value => round(value)), fill: "#4D69B8", valuesFormatCode: "0.0%" }],
  barOptions: { direction: "bar", grouping: "clustered", gapWidth: 44 },
  hasLegend: false,
  xAxis: { numberFormatCode: "0%", min: 0, max: isMsHkd ? Math.ceil(Math.max(...weights)/.1)*.1 : 0.7, majorUnit: 0.1, majorGridlines: { style: "solid", fill: "#d9e0e8", width: 1 }, textStyle: { fontSize: 11, fill: "#5d6878" } },
  yAxis: { textStyle: { fontSize: 12, fill: "#151b26" }, line: { style: "solid", fill: "#d9e0e8", width: 1 } },
  dataLabels: { showValue: true, position: "outEnd", textStyle: { fontSize: 11, bold: true, fill: "#151b26" } },
  chartFill: "#ffffff",
  chartLine: { fill: "none", width: 0 },
  plotAreaFill: "#ffffff",
});
setTable("tb/wfmpkre1", [
  ["Measure", "Value"],
  [isComposite ? "Largest component" : "Largest holding", `${securityName(holdings[0])} · ${percent(weights[0])}`],
  ["Top 5 concentration", percent(top5)],
  ["Top 10 concentration", percent(top10)],
  ["Net asset value", money2(nav)],
  [isComposite ? "Largest component type" : "Largest sector", `${largestSector.sector} · ${percent(largestSector.weight)}`],
]);
setTableCellFontSize("tb/wfmpkre1", 1, 1, 10);
widenSummaryTable("tb/wfmpkre1");

setText("sh/r2dkrq5g", isMsHkd ? `HKD portfolio ${percent(primary.portfolio_total_return)}. Benchmark comparison unavailable.` : `Portfolio ${percent(primary.portfolio_total_return)}   |   ${primaryBenchmarkName} ${percent(primary.benchmark_total_return)}   |   Excess ${points(primary.excess_total_return)}`);
const chartCategories = monthRows.map(row => {
  const [year, month] = row.date.split("-").map(Number);
  return `${new Intl.DateTimeFormat("en-GB", { month: "short", timeZone: "UTC" }).format(new Date(Date.UTC(year, month - 1, 1)))}-${String(year).slice(2)}`;
});
const performanceAxis = normalizedAxis(
  monthRows.flatMap(row => isMsHkd ? [row.portfolio] : [row.portfolio, row.benchmark]),
);
replaceChart("sl/gnmp4jqx", "ch/g36d8zy1", "line", {
  position: { left: 52.5, top: 141, width: 810, height: 270 },
  title: "Rebased performance comparison",
  titleTextStyle: { typeface: "Arial", fontSize: 15, bold: true, fill: "#151b26" },
  categories: chartCategories,
  series: [
    { name: isMsHkd ? 'Alpha World MS' : snapshot.subject_id, categories: chartCategories, values: monthRows.map(row => round(row.portfolio, 4)), line: { style: "solid", fill: isMsHkd ? '#111827' : "#45C3B7", width: 4 },
      // One month-end cannot form a line. Show its real observation, not a
      // fabricated inception/month-end segment. Other subjects stay unchanged.
      ...(isMsHkd && monthRows.length === 1 ? {marker:{symbol:'circle',size:7},fill:'#111827'} : {}),
    },
    ...(!isMsHkd ? [{ name: primaryBenchmarkName, categories: chartCategories, values: monthRows.map(row => round(row.benchmark, 4)), line: { style: "solid", fill: "#111827", width: 3 } }] : []),
  ],
  lineOptions: { smooth: !isMsHkd },
  hasLegend: true,
  legend: { position: "bottom", overlay: false, textStyle: { fontSize: 11, fill: "#151b26" } },
  xAxis: { title: { text: "Month-end", textStyle: { typeface: "Arial", fontSize: 11, fill: "#5d6878" } }, textStyle: { fontSize: 9, fill: "#5d6878" }, line: { style: "solid", fill: "#d9e0e8", width: 1 } },
  yAxis: { title: { text: "Rebased index", textStyle: { typeface: "Arial", fontSize: 11, fill: "#5d6878" } }, numberFormatCode: "0", min: performanceAxis.min, max: performanceAxis.max, majorUnit: performanceAxis.majorUnit, majorGridlines: { style: "solid", fill: "#d9e0e8", width: 1 }, textStyle: { fontSize: 10, fill: "#5d6878" } },
  chartFill: "#ffffff",
  chartLine: { fill: "none", width: 0 },
  plotAreaFill: "#ffffff",
});

setText("sh/6twve1oz", `Gross ${isComposite ? "component exposure" : "market value"} allocation | ${longDate(periodEnd)}`);
setText("sh/jq5w365o", percent(largestSector.weight));
setText("sh/i5wva1o3", largestSector.sector);
setText("sh/w3ed8r6x", `${Number(sector.classification_coverage_pct).toFixed(0)}%`);
replaceChart("sl/fu1gfa1s", "ch/m9w3y1cn", "bar", {
  position: { left: 52.5, top: 142.5, width: 570, height: 292.5 },
  title: isComposite ? "Component-type weights" : "Sector weights",
  titleTextStyle: { typeface: "Arial", fontSize: 15, bold: true, fill: "#151b26" },
  categories: sector.allocation.map(row => row.sector),
  series: [{
    name: "Weight",
    values: sector.allocation.map(row => round(row.weight)),
    fill: "#45C3B7",
    valuesFormatCode: "0.00%",
    dataLabelOverrides: sector.allocation.map((row, idx) => ({
      idx,
      text: `${(Number(row.weight) * 100).toFixed(2)}%`,
      position: "outEnd",
      showValue: false,
      textStyle: { fontSize: 11, bold: true, fill: "#151b26" },
    })),
  }],
  barOptions: { direction: "bar", grouping: "clustered", gapWidth: 48 },
  hasLegend: false,
  xAxis: { numberFormatCode: "0%", min: 0, max: 0.7, majorUnit: 0.1, majorGridlines: { style: "solid", fill: "#d9e0e8", width: 1 }, textStyle: { fontSize: 11, fill: "#5d6878" } },
  yAxis: { textStyle: { fontSize: 11, fill: "#151b26" }, line: { style: "solid", fill: "#d9e0e8", width: 1 } },
  dataLabels: { showValue: false, position: "outEnd", textStyle: { fontSize: 11, bold: true, fill: "#151b26" } },
  chartFill: "#ffffff",
  chartLine: { fill: "none", width: 0 },
  plotAreaFill: "#ffffff",
});

const monthlySecurity = [...monthlyReporting.security_contributions];
const detractors = monthlySecurity
  .filter(row => Number(row.contribution) < 0)
  .sort((a, b) => Number(a.contribution) - Number(b.contribution))
  .slice(0, 5);
const contributors = monthlySecurity
  .filter(row => Number(row.contribution) > 0)
  .sort((a, b) => Number(b.contribution) - Number(a.contribution))
  .slice(0, 5)
  .reverse();
const contributionRows = [...detractors, ...contributors];
if (!contributionRows.length) {
  contributionRows.push({ symbol: "No effect", contribution: 0 });
}
setText(
  "sh/2l4faloj",
  `Frongello-linked contribution to return | ${shortDate(monthlyReporting.period_start)} to ${shortDate(monthlyReporting.period_end)}`,
);
const contributionChart = replaceChart("sl/udsvah03", "ch/ut8nmhsr", "bar", {
  position: { left: 52.5, top: 165, width: 540, height: 270 },
  title: 'Top five contributors / detractors (magnitudes)',
  titleTextStyle: { typeface: "Arial", fontSize: 15, bold: true, fill: "#151b26" },
  categories: contributionRows.map(securityName),
  series: [{
    name: "Contribution",
    values: contributionRows.map(row => Math.abs(round(row.contribution))),
    fill: "#54BFAF",
    valuesFormatCode: "0.00%",
    points: contributionRows.map((row, idx) => ({
      idx,
      fill: Number(row.contribution) >= 0 ? "#00884A" : "#C94F52",
      line: { fill: Number(row.contribution) >= 0 ? "#00884A" : "#C94F52", width: 1 },
    })),
    dataLabelOverrides: contributionRows.map((row, idx) => ({
      idx,
      text: `${(Number(row.contribution) * 100).toFixed(2)}%`,
      position: "outEnd",
      showValue: false,
      textStyle: { fontSize: 10, bold: true, fill: "#151b26" },
    })),
  }],
  barOptions: { direction: "bar", grouping: "clustered", gapWidth: 38, varyColors: true },
  hasLegend: false,
  xAxis: {
    numberFormatCode: "0.00%",
    ...(isMsHkd ? {title:{text:'Magnitude (signed labels)',textStyle:{fontSize:10,fill:'#5d6878'}}} : {}),
    min: 0,
    majorGridlines: { style: "solid", fill: "#d9e0e8", width: 1 },
    textStyle: { fontSize: 10, fill: "#5d6878" },
    line: { style: "solid", fill: "#d9e0e8", width: 1 },
  },
  yAxis: {
    textStyle: { fontSize: 10.5, fill: "#151b26" },
    line: { style: "solid", fill: "#d9e0e8", width: 1 },
  },
  dataLabels: {
    showValue: false,
    position: "outEnd",
    textStyle: { fontSize: 10, bold: true, fill: "#151b26" },
  },
  chartFill: "#ffffff",
  chartLine: { fill: "none", width: 0 },
  plotAreaFill: "#ffffff",
});
applyPresentationChartFont(contributionChart, { fontFamily: "Arial" });
const largestContributor = monthlySecurity
  .filter(row => Number(row.contribution) > 0)
  .sort((a, b) => Number(b.contribution) - Number(a.contribution))[0];
const largestDetractor = monthlySecurity
  .filter(row => Number(row.contribution) < 0)
  .sort((a, b) => Number(a.contribution) - Number(b.contribution))[0];
setTable("tb/edoralwf", [
  ["Measure", "Contribution"],
  ["Largest contributor", largestContributor ? `${securityName(largestContributor)} · ${points(largestContributor.contribution)}` : "N/A"],
  ["Largest detractor", largestDetractor ? `${securityName(largestDetractor)} · ${points(largestDetractor.contribution)}` : "N/A"],
  ["Equity contribution", points(monthlyReporting.equity_contribution)],
  ["Residual contribution", points(monthlyReporting.residual_contribution)],
  ["Portfolio return", percent(monthlyReporting.portfolio_monthly_return)],
]);
setTableCellFontSize("tb/edoralwf", 1, 1, 10);
setTableCellFontSize("tb/edoralwf", 2, 1, 10);
widenSummaryTable("tb/edoralwf");
setText(
  "sh/onmxcv69",
  isComposite
    ? `Component effects use the approved fixed-unit sleeve values. The ${points(monthlyReporting.residual_contribution)} residual reconciles the sleeve effects to portfolio NAV return.`
    : attributionResidualNote(monthlyReporting, points),
);
presentation.resolve("sl/udsvah03").speakerNotes.textFrame.setText(
  `Deterministic source: frozen reporting snapshot monthly_reporting, method ${monthlyReporting.method_version}. ${isComposite ? "Approved fixed-unit component contributions" : "Daily prior-position mark-to-market contributions"} are Frongello-linked and reconcile to the compounded monthly NAV return through the residual.`,
);

const sectorRows = [...monthlyReporting.sector_contributions];
if (!sectorRows.length) sectorRows.push({ sector: "Unclassified", contribution: 0 });
setText(
  "sh/q9srixs3",
  `Portfolio ${isComposite ? "component-type" : "sector"} contributions and benchmark context | ${shortDate(monthlyReporting.period_start)} to ${shortDate(monthlyReporting.period_end)}`,
);
const sectorDriverChart = replaceChart("sl/m90b6t0r", "ch/e94rut4f", "bar", {
  position: { left: 52.5, top: 165, width: 540, height: 270 },
  title: isMsHkd ? 'Component-type contribution magnitudes (signed labels)' : `${isComposite ? "Component-type" : "Sector"} contribution to monthly return - green positive, red negative`,
  titleTextStyle: { typeface: "Arial", fontSize: 15, bold: true, fill: "#151b26" },
  categories: sectorRows.map(row => row.sector),
  series: [{
    name: "Contribution",
    values: sectorRows.map(row => Math.abs(round(row.contribution))),
    fill: "#54BFAF",
    valuesFormatCode: "0.00%",
    points: sectorRows.map((row, idx) => ({
      idx,
      fill: Number(row.contribution) >= 0 ? "#54BFAF" : "#C94F52",
      line: { fill: Number(row.contribution) >= 0 ? "#54BFAF" : "#C94F52", width: 1 },
    })),
    dataLabelOverrides: sectorRows.map((row, idx) => ({
      idx,
      text: `${(Number(row.contribution) * 100).toFixed(2)}%`,
      position: "outEnd",
      showValue: false,
      textStyle: { fontSize: 10, bold: true, fill: "#151b26" },
    })),
  }],
  barOptions: { direction: "bar", grouping: "clustered", gapWidth: 38, varyColors: true },
  hasLegend: false,
  xAxis: {
    numberFormatCode: "0.00%",
    min: 0,
    ...(isMsHkd ? {title:{text:'Magnitude (signed labels)',textStyle:{fontSize:10,fill:'#5d6878'}}} : {}),
    majorGridlines: { style: "solid", fill: "#d9e0e8", width: 1 },
    textStyle: { fontSize: 10, fill: "#5d6878" },
    line: { style: "solid", fill: "#d9e0e8", width: 1 },
  },
  yAxis: {
    textStyle: { fontSize: 10.5, fill: "#151b26" },
    line: { style: "solid", fill: "#d9e0e8", width: 1 },
  },
  dataLabels: {
    showValue: false,
    position: "outEnd",
    textStyle: { fontSize: 10, bold: true, fill: "#151b26" },
  },
  chartFill: "#ffffff",
  chartLine: { fill: "none", width: 0 },
  plotAreaFill: "#ffffff",
});
applyPresentationChartFont(sectorDriverChart, { fontFamily: "Arial" });
const excessMonthReturn = monthReturn == null || benchmarkMonthReturn == null
  ? null
  : Number(monthReturn) - Number(benchmarkMonthReturn);
setTable("tb/qpsbq1o7", [
  ["Measure", "Effect"],
  ["Portfolio return", percent(monthlyReporting.portfolio_monthly_return)],
  [`${primaryBenchmarkName} return`, percent(benchmarkMonthReturn)],
  ["Excess return", points(excessMonthReturn)],
  ["Equity contribution", points(monthlyReporting.equity_contribution)],
  ["Residual contribution", points(monthlyReporting.residual_contribution)],
]);
const strongestSector = [...sectorRows].sort((a, b) => Number(b.contribution) - Number(a.contribution))[0];
const weakestSector = [...sectorRows].sort((a, b) => Number(a.contribution) - Number(b.contribution))[0];
setText(
  "sh/cva9k7a9",
  `${strongestSector.sector} was the strongest ${allocationNoun} contributor at ${points(strongestSector.contribution)}. ${weakestSector.sector} was the weakest at ${points(weakestSector.contribution)}. Monthly excess return was ${points(excessMonthReturn)}.`,
);
presentation.resolve("sl/m90b6t0r").speakerNotes.textFrame.setText(
  `Deterministic source: frozen reporting snapshot monthly_reporting, method ${monthlyReporting.method_version}. Bars show portfolio ${allocationNoun} contribution, while the table provides ${primaryBenchmarkName} monthly-return context. This is contribution analysis, not benchmark constituent Brinson attribution.`,
);

const cashActivity = monthlyReporting.cash_activity;
const dividendRows = isMsHkd ? [{symbol:'Constant cash',amount:msModel.constant_cash}] : [...cashActivity.dividends_by_symbol].slice(0, 5);
if (!dividendRows.length) dividendRows.push({ symbol: isComposite ? "Not separately reported" : "None recorded", amount: 0 });
setText("sh/m58fih43", `${isComposite ? "Cash activity within underlying NAV sleeves" : "Cash income and financing items"} | ${reportMonth} ${periodEnd.slice(0, 4)}`);
const dividendChart = replaceChart("sl/jetc3ut0", "ch/2pk76d4z", "bar", {
  position: { left: 52.5, top: 165, width: 540, height: 270 },
  title: isMsHkd ? 'Constant cash per initial NAV 100' : isComposite ? "Underlying cash activity is included in sleeve NAV" : "Dividends received by security",
  titleTextStyle: { typeface: "Arial", fontSize: 15, bold: true, fill: "#151b26" },
  categories: dividendRows.map(row => row.symbol),
  series: [{
    name: isMsHkd ? 'Constant cash' : "Dividend",
    values: dividendRows.map(row => round(row.amount, 2)),
    fill: "#00884A",
    valuesFormatCode: isMsHkd ? '"HKD "0.00' : "$0.00",
    dataLabelOverrides: dividendRows.map((row, idx) => ({
      idx,
      text: isMsHkd ? money2(row.amount) : isComposite ? "N/A" : money2(row.amount),
      position: "outEnd",
      showValue: false,
      textStyle: { fontSize: 10, bold: true, fill: "#151b26" },
    })),
  }],
  barOptions: { direction: "column", grouping: "clustered", gapWidth: 48 },
  hasLegend: false,
  xAxis: {
    textStyle: { fontSize: 10.5, fill: "#151b26" },
    line: { style: "solid", fill: "#d9e0e8", width: 1 },
  },
  yAxis: {
    numberFormatCode: isMsHkd ? '"HKD "0' : "$0",
    min: 0,
    majorGridlines: { style: "solid", fill: "#d9e0e8", width: 1 },
    textStyle: { fontSize: 10, fill: "#5d6878" },
  },
  dataLabels: {
    showValue: false,
    position: "outEnd",
    textStyle: { fontSize: 10, bold: true, fill: "#151b26" },
  },
  chartFill: "#ffffff",
  chartLine: { fill: "none", width: 0 },
  plotAreaFill: "#ffffff",
});
applyPresentationChartFont(dividendChart, { fontFamily: "Arial" });
setTable("tb/i1onmh0n", isComposite ? [
  ["Composite cash policy", "Treatment"],
  ["Constant cash", isMsHkd ? "Zero interest" : "Non-interest-bearing"],
  ["Account-sleeve activity", "Included in account NAV"],
  ["Benchmark sleeves", "Price return only"],
  ["Separate cash breakdown", "Not available"],
] : [
  ["Monthly cash item", snapshot.reporting_currency],
  ["Dividends", money2(cashActivity.total_dividends)],
  ["Interest received", money2(cashActivity.interest_received)],
  ["Interest paid", money2(cashActivity.interest_paid)],
  ["Fees and withholding", money2(cashActivity.fees_and_withholding)],
  ["Net listed cash activity", money2(cashActivity.net_listed_cash_activity)],
]);
setText(
  "sh/o7axk7m9",
  isComposite
    ? "The constant cash sleeve earns no interest. Cash activity inside underlying accounts is already reflected in their NAV sleeves; it is not separately attributed here."
    : cashActivityNote(cashActivity, money2),
);
presentation.resolve("sl/jetc3ut0").speakerNotes.textFrame.setText(
  isComposite
    ? "Deterministic source: approved fixed-unit composite component ledger. The constant cash sleeve is non-interest-bearing; underlying account cash activity is reflected in its NAV sleeve."
    : "Deterministic source: frozen reporting snapshot monthly_reporting.cash_activity, derived from IBKR cash_transactions for the reporting month. External deposits, withdrawals and transfers are excluded.",
);

setTable("tb/m1gj2xgb", [
  ["Metric", "Value", "Definition / source"],
  ["Annualized return", percent(risk.annualized_return), isMsHkd ? 'NAV growth, 365.25 calendar days/year' : "Daily compound return, annualized"],
  ["Annualized volatility", percent(risk.annualized_volatility), "Daily return standard deviation, annualized"],
  ["Risk-free rate", percent(risk.risk_free_annual_rate), isMsHkd ? '1M HIBOR, inception-to-date average' : `UST 3-month par yield, ${snapshot.risk.risk_free.rate_date}`],
  ["Sharpe ratio", decimal(risk.sharpe_ratio), "Excess return / annualized volatility"],
  ["Daily VaR (95%)", percent(risk.var_95_daily), "Historical daily loss threshold"],
  ["Daily Expected Tail Loss", percent(risk.daily_expected_tail_loss), "Average loss beyond 95% VaR"],
  ["Win rate", percent(risk.win_rate), "Positive daily returns / return observations"],
  ["BLUE mean daily return", percent(risk.blue_mean_daily_return, 4), `Standard error ${percent(risk.blue_standard_error, 4)}`],
]);

setTable("tb/3ets36l8", [
  ["Metric", snapshot.subject_id, primaryBenchmarkName, secondaryBenchmarkName],
  [isMsHkd ? "Total return" : "Total return (common dates)", percent(primary.portfolio_total_return), percent(primary.benchmark_total_return), percent(secondary.benchmark_total_return)],
  ["Annualized return", percent(primary.portfolio_annualized_return), percent(primary.benchmark_annualized_return), percent(secondary.benchmark_annualized_return)],
  ["Annualized volatility", percent(primary.portfolio_annualized_volatility), percent(primary.benchmark_annualized_volatility), percent(secondary.benchmark_annualized_volatility)],
  ["Maximum drawdown", percent(primary.portfolio_max_drawdown), percent(primary.benchmark_max_drawdown), percent(secondary.benchmark_max_drawdown)],
  ["Excess total return", "—", points(primary.excess_total_return), points(secondary.excess_total_return)],
  ["Beta", "—", decimal(primary.beta), decimal(secondary.beta)],
  ["Correlation", "—", decimal(primary.correlation), decimal(secondary.correlation)],
  ["Tracking error", "—", percent(primary.tracking_error), percent(secondary.tracking_error)],
  ["Information ratio", "—", decimal(primary.information_ratio), decimal(secondary.information_ratio)],
  ["Relative win rate", "—", percent(primary.relative_win_rate), percent(secondary.relative_win_rate)],
]);

setText("sh/2t4fyl8z", percent(risk.expected_maximum_drawdown_1y));
setText("sh/d0fydk7m", isMsHkd ? 'Drawdown simulations unavailable' : `${Number(risk.drawdown_simulation_paths).toLocaleString("en-US")} simulations; ${risk.drawdown_horizon_sessions} sessions`);
setText("sh/cvylkn2d", percent(risk.actual_maximum_drawdown_1y));
setText("sh/lkrmhsju", percent(risk.tail_maximum_drawdown_1y));
setText("sh/h4nm5sba", percent(risk.crisis_maximum_drawdown));
setText("sh/25w3excv", isMsHkd ? (msModel.crisis_available ? `Hypothetical HKD stress of components dated ${crisis.holdings_as_of}, using documented proxies and historical FX. HKD cash stays flat; no projected financing charge. Simulated drawdowns remain unavailable.` : 'Crisis replay and simulated drawdown results are unavailable for this HKD history. N/A indicates unavailable, not zero loss.') : `The crisis replay applies ${holdingNoun}s dated ${crisis.holdings_as_of} to the 15 September 2008 to 9 March 2009 period. It uses ${crisis.direct_components} direct histories and ${crisis.proxy_components} documented proxies. Cash remains flat.`);
setText("sh/p87m9cb6", `Observed full-period maximum drawdown: ${percent(risk.maximum_drawdown)}`);
setTable("tb/3mtobupc", [
  ["Input / control", "Value"],
  ["Return observations", String(risk.return_observations)],
  ["Block length", isMsHkd ? 'N/A' : `${risk.drawdown_block_length} sessions`],
  ["Simulation paths", isMsHkd ? 'N/A' : Number(risk.drawdown_simulation_paths).toLocaleString("en-US")],
  ["Tail confidence", percent(risk.drawdown_tail_confidence, 0)],
  ["Horizon", isMsHkd ? 'N/A' : `${risk.drawdown_horizon_sessions} sessions`],
]);

const monthlyByYear = new Map();
for (const row of portfolioMonthRows) {
  const [year, month] = row.date.split("-").map(Number);
  if (!monthlyByYear.has(year)) monthlyByYear.set(year, Array(12).fill("—"));
  monthlyByYear.get(year)[month - 1] = row.portfolio_monthly_return == null ? "—" : percent(row.portfolio_monthly_return, 1);
}
setTable("tb/a9srud0n", [
  ["Year", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
  ["2025", ...(monthlyByYear.get(2025) ?? Array(12).fill("—"))],
  ["2026", ...(monthlyByYear.get(2026) ?? Array(12).fill("—"))],
]);
setText("sh/361gvilk", `Total return ${percent(isMsHkd ? risk.total_return : portfolioTotalReturn)}  •  Annualized return ${percent(risk.annualized_return)}  •  Win rate ${percent(risk.win_rate)}`);
setText("sh/25sf2d4z", isMsHkd ? 'January 2025 is a partial month from inception on 20 January. Monthly returns use observed HKD NAV.' : portfolioMonthlyNote(portfolioModel, shortDate));

setTable("tb/5kb2xwby", [
  ["Control", "Status", "Evidence"],
  [isMsHkd ? 'Composite NAV coverage' : "IBKR NAV coverage", "PASS", isMsHkd ? `${msModel.calculation.nav_observations} observations; ${msModel.calculation.start} to ${dataAsOf}` : `${snapshot.portfolio_summary.nav_coverage.observations} observations; ${snapshot.portfolio_summary.nav_coverage.first_date} to ${dataAsOf}`],
  [isComposite ? "Components" : "Positions", "PASS", `${holdings.length} ${holdingNoun}s as of ${dataAsOf}`],
  ["Risk-free rate", "PASS", isMsHkd ? `1M HIBOR, period average ${percent(risk.risk_free_annual_rate)}` : `UST 3-month, ${snapshot.risk.risk_free.rate_date}, ${percent(snapshot.risk.risk_free.annual_rate)}`],
  ["Primary benchmark", isMsHkd ? 'UNAVAILABLE' : "AVAILABLE", primaryBenchmarkName],
  ["Secondary benchmark", isMsHkd ? 'UNAVAILABLE' : "AVAILABLE", secondaryBenchmarkName],
  [isComposite ? "Component classification" : "Sector classification", "PASS", `${Number(sector.classification_coverage_pct).toFixed(0)}% ${holdingNoun} coverage`],
  ["Crisis replay", isMsHkd ? (msModel.crisis_available ? 'WARN' : 'UNAVAILABLE') : "PASS", isMsHkd ? (msModel.crisis_available ? `${crisis.observations} HKD stress sessions; documented proxies` : 'No qualified HKD replay') : `${crisis.direct_components} direct histories and ${crisis.proxy_components} proxies`],
]);

setText("sh/ytc7a1gv", `• ${monthPhrase}`);
setText("sh/d83q1wfa", `• The full-period total return is ${percent(portfolioTotalReturn)}.`);
setText("sh/c7up8ryp", `• The portfolio's positive-day win rate is ${percent(risk.win_rate)}.`);
setText("sh/2dsn2xwn", `• Upside capture versus ${primaryBenchmarkName} is ${percent(primary.upside_capture_pct / 100)}.`);
setText("sh/e98nydwb", `• Cumulative return ${relativePhrase} ${primaryBenchmarkName} by ${points(Math.abs(primary.excess_total_return))}.`);
setText("sh/fah47idw", `• Volatility is ${percent(primary.portfolio_annualized_volatility)} versus ${percent(primary.benchmark_annualized_volatility)} for ${primaryBenchmarkName}.`);
setText("sh/gbq50neh", `• ${largestSector.sector} is ${percent(largestSector.weight)} of gross ${isComposite ? "sleeve exposure" : "market value"}.`);
setText("sh/1cj69sv2", `• The five largest ${holdingNoun}s account for ${percent(top5)} of gross exposure.`);
setText("sh/ixsj690v", `• Beta versus ${primaryBenchmarkName} is ${decimal(primary.beta)}, and correlation is ${decimal(primary.correlation)}.`);
setText("sh/50j2hozm", `• Daily expected tail loss is ${percent(risk.daily_expected_tail_loss)}.`);
setText("sh/kza18ji1", `• The 95% tail one-year drawdown estimate is ${percent(risk.tail_maximum_drawdown_1y)}.`);
setText("sh/vuhkbehk", `• The crisis replay drawdown is ${percent(risk.crisis_maximum_drawdown)} using current ${holdingNoun}s and documented proxies.`);
const deterministicBottomLine = isMsHkd ? `HKD total return is ${percent(risk.total_return)} with volatility of ${percent(risk.annualized_volatility)}. ${msModel.crisis_available ? `Hypothetical crisis drawdown is ${percent(risk.crisis_maximum_drawdown)}.` : 'Crisis replay remains unavailable.'} Benchmark-relative results remain unavailable. NAV interest effects are included without an additional financing charge.` : genericSummaryLabel(primary, primaryBenchmarkName);
const agentSummary = commentary?.executive_summary?.trim();
if (agentSummary && (agentSummary.length > 420 || /[0-9%$]/.test(agentSummary))) {
  throw new Error("Agent commentary violates the qualitative narrative policy");
}
setText("sh/at8j290z", agentSummary ? (isMsHkd ? `Portfolio analyst: ${agentSummary}` : boundedGenericCommentary(`Portfolio analyst: ${agentSummary}`)) : deterministicBottomLine);
setText("sh/rip4retw", "Results ");

if (isMsHkd) {
  setText('sh/2dsn2xwn','• Upside capture is unavailable without a qualified benchmark.');
  setText('sh/e98nydwb','• Benchmark excess return is unavailable.');
  setText('sh/ixsj690v','• Benchmark beta and correlation are unavailable.');
  setText('sh/kza18ji1','• Simulated drawdown estimates are unavailable.');
  setText('sh/vuhkbehk',msModel.crisis_available ? `• Hypothetical HKD crisis drawdown is ${percent(risk.crisis_maximum_drawdown)}; documented proxies are used.` : '• Crisis replay is unavailable for the HKD history.');
  // Replace fixed reference captions, without changing positions or typography.
  const fixed=await presentation.inspect({kind:'textbox',maxChars:1000000});
  const replacements=new Map([
    ['Sector Drivers of Relative Performance','Component Drivers of Monthly Return'],
    ['EXCESS VS S&P 500','BENCHMARK EXCESS'],['Common-date return','Unavailable'],
    ['IBKR NAV','Composite NAV, inception 100'],
    ['Growth of 100 — portfolio versus S&P 500 Price Index','HKD NAV, inception 100. Benchmark unavailable.'],
    ['Primary and secondary benchmarks selectable in the staging website','Benchmark comparisons unavailable for qualified HKD history'],
    ['Observed and simulated one-year loss severity',msModel.crisis_available ? 'Observed drawdown and hypothetical HKD crisis stress. Simulations unavailable.' : 'Observed drawdown. Simulated and crisis results unavailable.'],
    ['Latest rolling 1-year window','Unavailable'],['95% tail of simulated outcomes','Unavailable'],
    ['15 Sep 2008 to 9 Mar 2009 replay',msModel.crisis_available ? '15 Sep 2008 to 9 Mar 2009 HKD stress' : 'Unavailable'],
  ]);
  for (const line of fixed.ndjson.split('\n').filter(Boolean)) {
    const item=JSON.parse(line);const old=item.text || item.textPreview;
    if(replacements.has(old)) setText(item.id,replacements.get(old));
  }
}

// The template's disclaimer heading drops its first letter. Only that exact
// heading is corrected; the disclaimer wording itself is left untouched.
{
  const headings = await presentation.inspect({kind:'textbox',maxChars:1000000});
  for (const line of headings.ndjson.split('\n').filter(Boolean)) {
    const item = JSON.parse(line);
    if (String(item.text || item.textPreview || '').replace(/\s+/g, ' ').trim() === 'llustrative examples only') {
      setText(item.id, 'Illustrative examples only');
    }
  }
}

if (snapshot.subject_id === "U16088606") {
  const identityCheck = await presentation.inspect({
    kind: "textbox,shape",
    search: "Greater China|FOCUS US Tech|U.S. Technology and Biotechnology",
    maxChars: 30000,
  });
  if (!identityCheck.ndjson.includes("Greater China")) {
    throw new Error("Greater China strategy label is missing from the presentation");
  }
  if (/FOCUS US Tech|U\.S\. Technology and Biotechnology/i.test(identityCheck.ndjson)) {
    throw new Error("Greater China presentation contains the wrong strategy identity");
  }
}

const outputDir = path.dirname(path.resolve(finalPath));
const stagingDir = path.resolve(
  ".work",
  "presentation-validation",
  `${path.basename(finalPath, ".pptx")}-${randomUUID()}`,
);
await fs.mkdir(stagingDir, { recursive: true });
await fs.mkdir(outputDir, { recursive: true });
let candidatePath = path.join(stagingDir, `${path.basename(finalPath, ".pptx")}.candidate.pptx`);
await (await PresentationFile.exportPptx(presentation)).save(candidatePath);

if (isMsHkd) {
  const workbookPath = path.join(stagingDir, `${path.basename(finalPath, '.pptx')}.workbooks.pptx`);
  const receipt = JSON.parse(execFileSync(process.env.RUNTIME_PYTHON || process.env.PYTHON_BINARY || 'python',
    ['-m', 'ibkr_etl.ms_chart_workbooks', candidatePath, workbookPath], {encoding: 'utf8'}));
  if (receipt.status !== 'passed' || receipt.embedded_workbooks_verified !== 6) {
    throw new Error('MS editable chart workbook creation did not pass');
  }
  await fs.writeFile(path.join(stagingDir, `${path.basename(finalPath)}.chart-workbooks.json`), JSON.stringify(receipt, null, 2));
  candidatePath = workbookPath;
}

if (process.env.SKILL_DIR && process.env.RUNTIME_PYTHON && process.env.RUNTIME_NODE_MODULES) {
  const { finalizePresentation } = await import(pathToFileURL(
    path.join(process.env.SKILL_DIR, "container_tools/artifact_tool_utils.mjs"),
  ).href);
  await finalizePresentation({
  workspaceDir: path.resolve("."),
  candidatePath,
  finalPath: path.resolve(finalPath),
  pythonExecutable: process.env.RUNTIME_PYTHON,
  integrityValidatorPath: path.join(process.env.SKILL_DIR, "container_tools/inspect_presentation_package_integrity.py"),
  layoutValidatorPath: path.join(process.env.SKILL_DIR, "container_tools/inspect_presentation_layout_geometry.py"),
  layoutArgs: [
    "--expected-slide-size-emu", "9144000,5143500",
    "--validate-bullet-geometry",
    "--validate-heading-fit",
    ...[3, 5, 6, 9, 10, 11, 12, 13, 14, 15, 16].flatMap(number => ["--require-native-table-slide", String(number)]),
  ],
  explicitTotalSlideCount: 20,
  requiredNativeTableOwnerSlides: [3, 5, 6, 9, 10, 11, 12, 13, 14, 15, 16],
  requiredNativeChartOwnerSlides: [6, 7, 8, 9, 10, 11],
  requiredEmbeddedWorkbookChartOwnerSlides: [],
  sourceTemplatePath: path.resolve(sourcePath),
  requiredTemplateReferenceSlides: Array.from({ length: 20 }, (_, index) => index + 1),
  minimumTemplateCoverageRatio: 1,
  requireExactTemplateDimensions: true,
  requireTemplatePlaceholderGeometry: false,
  requirePhotographicBackground: false,
  materializeLiteralChartWorkbooks: true,
  ...(isMsHkd ? {fontPolicy:{basis:'reference',families:['Arial','Aptos Display','Georgia'],referencePath:path.resolve(sourcePath),
    referenceSha256:'4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191'}} : {}),
  verifyArtifactToolImport: true,
    receiptPath: path.join(stagingDir, `${path.basename(finalPath)}.validation.json`),
  });
} else {
  await fs.rename(candidatePath, path.resolve(finalPath));
}

if (isMsHkd) {
  const workbooks = JSON.parse(execFileSync(process.env.RUNTIME_PYTHON || process.env.PYTHON_BINARY || 'python',
    ['-m', 'ibkr_etl.ms_chart_workbooks', path.resolve(finalPath)], {encoding: 'utf8'}));
  if (workbooks.status !== 'passed' || workbooks.embedded_workbooks_verified !== 6) {
    throw new Error('MS final editable chart workbook acceptance did not pass');
  }
  const audit=JSON.parse(execFileSync(process.env.RUNTIME_PYTHON || process.env.PYTHON_BINARY || 'python',
    [path.resolve('scripts/audit_alpha_ms_presentation.py'),path.resolve(finalPath),snapshotPath],{encoding:'utf8'}));
  if(audit.status!=='passed') throw new Error('MS exported financial evidence did not pass');
  await fs.writeFile(path.join(stagingDir,`${path.basename(finalPath)}.ms-financial-validation.json`),JSON.stringify(audit,null,2));
}

console.log(JSON.stringify({
  status: "success",
  period_end: periodEnd,
  data_as_of: dataAsOf,
  output_path: path.resolve(finalPath),
  slides: 20,
}));
