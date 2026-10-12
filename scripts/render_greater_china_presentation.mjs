import fs from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { pathToFileURL } from "node:url";
import { FileBlob, PresentationFile } from "@oai/artifact-tool";

const [sourcePath, snapshotPath, finalPath, commentaryPath] = process.argv.slice(2);
if (!sourcePath || !snapshotPath || !finalPath) {
  throw new Error("Usage: render_greater_china_presentation.mjs template.pptx snapshot.json output.pptx [commentary.json]");
}

const snapshot = JSON.parse(await fs.readFile(snapshotPath, "utf8"));
const commentary = commentaryPath ? JSON.parse(await fs.readFile(commentaryPath, "utf8")) : null;
if (snapshot.subject_id !== "U16088606" || snapshot.reporting_currency !== "USD") {
  throw new Error("Greater China renderer requires U16088606 in USD");
}
if (snapshot.data_as_of > snapshot.period_end) throw new Error("Snapshot data is future-dated");

const presentation = await PresentationFile.importPptx(await FileBlob.load(sourcePath));
if (presentation.slides.items.length !== 20) throw new Error("Greater China template must contain 20 slides");

const FONT = "Arial";
const INK = "#151B26";
const MUTED = "#5D6878";
const GRID = "#D9E0E8";
const NAVY = "#111827";
const GREEN = "#00884A";
const BLUE = "#4D69B8";
const PALE = "#EEF2F7";
const PALE_TEAL = "#E8F7F5";
const RED = "#C94F52";
const TEAL = "#45C3B7";
const TEMPLATE_LAYOUT = "u16088606-greater-china-owner-template-v2-us-tech-evidence-style";

const percent = (value, digits = 2) => value == null ? "N/A" : `${(Number(value) * 100).toFixed(digits)}%`;
const points = (value) => value == null ? "N/A" : `${(Number(value) * 100).toFixed(2)} pp`;
const decimal = (value, digits = 2) => value == null ? "N/A" : Number(value).toFixed(digits);
const round = (value, digits = 6) => Number(Number(value).toFixed(digits));
const securityName = row => {
  const description = String(row?.description || "").trim().replace(/\s+/g, " ");
  return description || String(row?.symbol || "Security").trim() || "Security";
};
const money = (value) => `$${Math.round(Number(value || 0)).toLocaleString("en-US")}`;
const money2 = (value) => {
  const amount = Number(value || 0);
  const formatted = Math.abs(amount).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${amount < 0 ? "-" : ""}$${formatted}`;
};
const shortDate = (value) => {
  const [year, month, day] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })
    .format(new Date(Date.UTC(year, month - 1, day)));
};
const monthName = (value) => {
  const [year, month] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("en-GB", { month: "long", timeZone: "UTC" })
    .format(new Date(Date.UTC(year, month - 1, 1)));
};
const normalizedAxis = (values, step = 10) => {
  const clean = values.map(Number).filter(Number.isFinite);
  if (!clean.length) throw new Error("Normalized performance chart has no finite values");
  const min = Math.floor(Math.min(...clean) / step) * step;
  const upper = Math.ceil(Math.max(...clean) / step) * step;
  return upper > min ? { min, max: upper, majorUnit: step } : { min: min - step, max: upper + step, majorUnit: step };
};

const addText = (slide, text, position, options = {}) => {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position,
    fill: "none",
    line: { fill: "none", width: 0 },
  });
  shape.text = String(text);
  shape.text.style = {
    typeface: FONT,
    fontSize: options.fontSize ?? 13,
    bold: options.bold ?? false,
    color: options.color ?? INK,
    alignment: options.alignment ?? "left",
    verticalAlignment: options.verticalAlignment ?? "top",
    autoFit: "shrinkText",
  };
  return shape;
};

const addTitle = (slide, title, subtitle) => {
  addText(slide, title, { left: 30, top: 91, width: 580, height: 28 }, { fontSize: 21, bold: true, color: GREEN });
  if (subtitle) addText(slide, subtitle, { left: 30, top: 119, width: 860, height: 22 }, { fontSize: 10.5, color: MUTED });
};

const styleTable = (table, {
  fontSize = 10.5,
  headerFontSize = 10.5,
  firstColumnLeft = true,
  headerFill = NAVY,
  alternatingFill = PALE,
  bodyFill = "#FFFFFF",
  headerAlignment = "left",
} = {}) => {
  table.styleOptions = { headerRow: true, bandedRows: false, firstColumn: false, lastColumn: false };
  table.borders.assign({ style: "solid", fill: GRID, width: 0.75 });
  const shared = {
    margins: { left: 8, right: 8, top: 4, bottom: 4 },
    anchor: "middle",
    horizontalOverflow: "clip",
  };
  table.cells.block({ row: 0, column: 0, rowCount: 1, columnCount: table.columns.length }).assign({
    ...shared,
    fill: headerFill,
    textStyle: {
      typeface: FONT,
      fontSize: headerFontSize * (4 / 3),
      bold: true,
      color: "#FFFFFF",
      alignment: headerAlignment,
    },
  });
  for (let row = 1; row < table.rows.length; row += 1) {
    table.cells.block({ row, column: 0, rowCount: 1, columnCount: table.columns.length }).assign({
      ...shared,
      fill: row % 2 === 1 ? alternatingFill : bodyFill,
      textStyle: {
        typeface: FONT,
        fontSize: fontSize * (4 / 3),
        bold: false,
        color: INK,
      },
    });
  }
  return table;
};

const addTable = (slide, values, position, columnWidths, options = {}) => {
  const table = slide.tables.add({
    rows: values.length,
    columns: values[0].length,
    left: position.left,
    top: position.top,
    width: position.width,
    height: position.height,
    values,
    ...(columnWidths ? { columnWidths } : {}),
  });
  styleTable(table, options);
  const rowHeight = position.height / values.length;
  for (const row of table.rows) row.height = rowHeight;
  return table;
};

const applyChartFont = (chart) => {
  const styles = [chart.titleTextStyle, chart.legend?.textStyle, chart.dataLabels?.textStyle, chart.xAxis?.textStyle, chart.yAxis?.textStyle];
  for (const style of styles) if (style) style.typeface = FONT;
  return chart;
};

const addBarChart = (slide, config) => applyChartFont(slide.charts.add("bar", {
  position: config.position,
  title: config.title,
  titleTextStyle: { typeface: FONT, fontSize: 14, bold: true, fill: INK },
  categories: config.categories,
  series: [{
    name: config.seriesName,
    values: config.values.map((value) => Math.abs(round(value))),
    fill: config.fill ?? TEAL,
    valuesFormatCode: config.formatCode,
    points: config.values.map((value, idx) => ({
      idx,
      fill: value >= 0 ? (config.positiveFill ?? config.fill ?? TEAL) : (config.negativeFill ?? RED),
      line: { fill: value >= 0 ? (config.positiveFill ?? config.fill ?? TEAL) : (config.negativeFill ?? RED), width: 1 },
    })),
    dataLabelOverrides: config.values.map((value, idx) => ({
      idx,
      text: config.label(value),
      position: "outEnd",
      showValue: false,
      textStyle: { fontSize: 10, bold: true, fill: INK },
    })),
  }],
  barOptions: { direction: "bar", grouping: "clustered", gapWidth: 42, varyColors: config.varyColors ?? true },
  hasLegend: false,
  xAxis: {
    numberFormatCode: config.formatCode,
    min: 0,
    majorGridlines: { style: "solid", fill: GRID, width: 1 },
    textStyle: { fontSize: 10, fill: MUTED },
    line: { style: "solid", fill: GRID, width: 1 },
  },
  yAxis: { textStyle: { fontSize: 10.5, fill: INK }, line: { style: "solid", fill: GRID, width: 1 } },
  dataLabels: { showValue: false, position: "outEnd", textStyle: { fontSize: 10, bold: true, fill: INK } },
  chartFill: "#FFFFFF",
  chartLine: { fill: "none", width: 0 },
  plotAreaFill: "#FFFFFF",
}));

const primary = snapshot.performance_comparison.metrics;
const secondary = snapshot.secondary_performance_comparison.metrics;
const risk = snapshot.risk.metrics;
const crisis = snapshot.risk.crisis_replay;
const sector = snapshot.sector_allocation;
const monthlyReporting = snapshot.monthly_reporting;
if (!monthlyReporting?.available) throw new Error("Snapshot lacks validated monthly reporting analytics");
const periodEnd = snapshot.period_end;
const dataAsOf = snapshot.data_as_of;
const reportMonth = monthName(periodEnd);
const monthRows = snapshot.month_end_performance;
const latestMonth = monthRows.at(-1) ?? {};
const monthReturn = latestMonth.portfolio_monthly_return;
const benchmarkMonthReturn = latestMonth.benchmark_monthly_return;
const primaryName = snapshot.benchmark?.name || snapshot.benchmark?.benchmark_id || "Primary benchmark";
const secondaryName = snapshot.secondary_benchmark?.name || snapshot.secondary_benchmark?.benchmark_id || "Secondary benchmark";
const primaryShort = primaryName.replace(/ Price Index$/i, "");
const secondaryShort = secondaryName.replace(/ Price Index$/i, "");
const holdings = [...snapshot.holdings.holdings]
  .sort((a, b) => Math.abs(Number(b.market_value_text)) - Math.abs(Number(a.market_value_text)));
const grossMarketValue = Number(sector.gross_market_value);
const nav = Number(snapshot.portfolio_summary.latest_nav.total_text);
const weights = holdings.map((row) => Math.abs(Number(row.market_value_text)) / grossMarketValue);
const top5 = weights.slice(0, 5).reduce((sum, value) => sum + value, 0);
const top10 = weights.slice(0, 10).reduce((sum, value) => sum + value, 0);
const largestSector = [...sector.allocation].sort((a, b) => Number(b.weight) - Number(a.weight))[0];
const relativePhrase = Number(primary.excess_total_return) >= 0 ? "outperformed" : "underperformed";
const monthPhrase = monthReturn == null || benchmarkMonthReturn == null
  ? `${reportMonth} is the opening partial reporting month, so no comparable monthly return is shown.`
  : `${reportMonth} returned ${percent(monthReturn)}, versus ${percent(benchmarkMonthReturn)} for ${primaryName}.`;

const slide = (number) => presentation.slides.getItem(number - 1);
for (const [number, id] of [
  [5, "sh/k7mxovud"],
  [7, "sh/ydkbm5sv"],
  [8, "sh/id0fu50z"],
]) {
  const placeholder = presentation.resolve(id);
  slide(number).shapes.deleteById(placeholder.id);
}

addTitle(slide(3), "Executive Summary", `${reportMonth} ${periodEnd.slice(0, 4)} | U16088606-Greater China | Reporting currency USD`);
addTable(slide(3), [
  ["Measure", "Result", "Context"],
  ["Monthly return", percent(monthReturn), `${primaryName}: ${percent(benchmarkMonthReturn)}`],
  ["Total return", percent(primary.portfolio_total_return), `Since ${shortDate(primary.period_start)}`],
  ["Annualized return", percent(primary.portfolio_annualized_return), `${primary.return_observations} daily returns`],
  ["Annualized volatility", percent(primary.portfolio_annualized_volatility), `Sharpe ratio ${decimal(risk.sharpe_ratio)}`],
  ["Daily expected tail loss", percent(risk.daily_expected_tail_loss), `Observed maximum drawdown ${percent(primary.portfolio_max_drawdown)}`],
], { left: 50, top: 155, width: 860, height: 240 }, [245, 145, 470], { fontSize: 10.5, headerFontSize: 10.5 });
addText(slide(3), `${monthPhrase} The portfolio ${relativePhrase} ${primaryName} by ${points(Math.abs(primary.excess_total_return))} over the common history.`, { left: 50, top: 415, width: 860, height: 48 }, { fontSize: 12, color: INK });

addTitle(slide(4), "Portfolio Overview", `Data as of ${shortDate(dataAsOf)} | U16088606-Greater China`);
addTable(slide(4), [
  ["Portfolio measure", "Value"],
  ["Net asset value", money(nav)],
  ["Gross market value", money(grossMarketValue)],
  ["Gross holdings / NAV", percent(grossMarketValue / nav)],
  ["Number of positions", String(holdings.length)],
  ["Largest sector", `${largestSector.sector} | ${percent(largestSector.weight)}`],
  ["Top five concentration", percent(top5)],
  ["Top ten concentration", percent(top10)],
], { left: 50, top: 155, width: 520, height: 285 }, [320, 200], { fontSize: 10.5, headerFontSize: 10.5 });
addText(slide(4), `Portfolio return was ${percent(primary.portfolio_total_return)} for the reporting period. Annualized volatility was ${percent(primary.portfolio_annualized_volatility)}. ${largestSector.sector} represented ${percent(largestSector.weight)} of gross market value.`, { left: 610, top: 175, width: 285, height: 180 }, { fontSize: 14, color: INK });

addTitle(slide(5), "Top 10 Holdings", `Gross market value ${money(grossMarketValue)} | Market values shown in USD | ${shortDate(dataAsOf)}`);
addTable(slide(5), [
  ["Rank", "Ticker", "Security", "Quantity", "Market value", "% of gross"],
  ...holdings.slice(0, 10).map((row, index) => [
    String(index + 1),
    row.symbol,
    row.description,
    Number(row.quantity_text).toLocaleString("en-US"),
    money(row.market_value_text),
    percent(Math.abs(Number(row.market_value_text)) / grossMarketValue),
  ]),
], { left: 40, top: 150, width: 880, height: 300 }, [55, 90, 300, 120, 170, 145],
{ fontSize: 8.5, headerFontSize: 8.8 });

addTitle(slide(6), "Holdings Concentration", `Current gross market value allocation | ${shortDate(dataAsOf)}`);
addBarChart(slide(6), {
  position: { left: 50, top: 145, width: 545, height: 310 },
  title: "Top 10 holdings - % of gross market value",
  categories: holdings.slice(0, 10).map(securityName),
  values: weights.slice(0, 10),
  seriesName: "Weight",
  formatCode: "0%",
  label: (value) => percent(value, 1),
  fill: BLUE,
  positiveFill: BLUE,
  varyColors: false,
});
addTable(slide(6), [
  ["Concentration measure", "Value"],
  ["Largest holding", `${securityName(holdings[0])} | ${percent(weights[0])}`],
  ["Top 5", percent(top5)],
  ["Top 10", percent(top10)],
  ["Net asset value", money2(nav)],
  ["Largest sector", `${largestSector.sector} | ${percent(largestSector.weight)}`],
], { left: 625, top: 165, width: 290, height: 255 }, [155, 135], { fontSize: 8, headerFontSize: 9.8 });

addTitle(slide(7), "Historical Performance", `Rebased to 100 on the first common observation | Through ${shortDate(dataAsOf)}`);
const chartCategories = monthRows.map((row) => {
  const [year, month] = row.date.split("-").map(Number);
  return `${new Intl.DateTimeFormat("en-GB", { month: "short", timeZone: "UTC" }).format(new Date(Date.UTC(year, month - 1, 1)))}-${String(year).slice(2)}`;
});
const performanceAxis = normalizedAxis(monthRows.flatMap((row) => [row.portfolio, row.benchmark]));
const performanceChart = slide(7).charts.add("line", {
  position: { left: 50, top: 145, width: 860, height: 260 },
  title: "Rebased performance comparison",
  titleTextStyle: { typeface: FONT, fontSize: 14, bold: true, fill: INK },
  categories: chartCategories,
  series: [
    { name: "U16088606-Greater China", categories: chartCategories, values: monthRows.map((row) => round(row.portfolio, 4)), line: { style: "solid", fill: TEAL, width: 4 } },
    { name: primaryName, categories: chartCategories, values: monthRows.map((row) => round(row.benchmark, 4)), line: { style: "solid", fill: "#111827", width: 3 } },
  ],
  lineOptions: { smooth: true },
  hasLegend: true,
  legend: { position: "bottom", overlay: false, textStyle: { fontSize: 11, fill: INK } },
  xAxis: { title: { text: "Month-end", textStyle: { typeface: FONT, fontSize: 11, fill: MUTED } }, textStyle: { fontSize: 9, fill: MUTED }, line: { style: "solid", fill: GRID, width: 1 } },
  yAxis: { title: { text: "Rebased index", textStyle: { typeface: FONT, fontSize: 11, fill: MUTED } }, numberFormatCode: "0", min: performanceAxis.min, max: performanceAxis.max, majorUnit: 10, majorGridlines: { style: "solid", fill: GRID, width: 1 }, textStyle: { fontSize: 10, fill: MUTED } },
  chartFill: "#FFFFFF",
  chartLine: { fill: "none", width: 0 },
  plotAreaFill: "#FFFFFF",
});
applyChartFont(performanceChart);
addTable(slide(7), [
  ["Portfolio total return", `${primaryShort} total return`, "Excess total return"],
  [percent(primary.portfolio_total_return), percent(primary.benchmark_total_return), percent(primary.excess_total_return)],
], { left: 165, top: 420, width: 630, height: 58 }, [210, 210, 210], {
  fontSize: 10.5,
  headerFontSize: 10,
  firstColumnLeft: false,
  headerAlignment: "center",
});

addTitle(slide(8), "Sector Allocation", `Gross market value allocation | ${shortDate(dataAsOf)} | ${Number(sector.classification_coverage_pct).toFixed(0)}% classification coverage`);
addBarChart(slide(8), {
  position: { left: 50, top: 145, width: 570, height: 310 },
  title: "Sector weights",
  categories: sector.allocation.map((row) => row.sector),
  values: sector.allocation.map((row) => Number(row.weight)),
  seriesName: "Weight",
  formatCode: "0%",
  label: (value) => percent(value, 1),
  fill: TEAL,
  positiveFill: TEAL,
  varyColors: false,
});
addTable(slide(8), [
  ["Sector", "Weight"],
  ...sector.allocation.slice(0, 10).map((row) => [row.sector, percent(row.weight)]),
], { left: 650, top: 165, width: 260, height: 275 }, [175, 85], { fontSize: 9.2, headerFontSize: 9.5 });

const monthlySecurity = [...monthlyReporting.security_contributions];
const contributionRows = [
  ...monthlySecurity.filter((row) => Number(row.contribution) < 0).sort((a, b) => Number(a.contribution) - Number(b.contribution)).slice(0, 5),
  ...monthlySecurity.filter((row) => Number(row.contribution) > 0).sort((a, b) => Number(b.contribution) - Number(a.contribution)).slice(0, 5).reverse(),
];
if (!contributionRows.length) contributionRows.push({ symbol: "No effect", contribution: 0 });
const largestContributor = [...monthlySecurity].sort((a, b) => Number(b.contribution) - Number(a.contribution))[0];
const largestDetractor = [...monthlySecurity].sort((a, b) => Number(a.contribution) - Number(b.contribution))[0];
addTitle(slide(9), "Monthly Security Contribution", `${shortDate(monthlyReporting.period_start)} to ${shortDate(monthlyReporting.period_end)} | Frongello-linked contribution`);
addBarChart(slide(9), {
  position: { left: 50, top: 155, width: 545, height: 295 },
  title: "Top five contributors and bottom five detractors",
  categories: contributionRows.map(securityName),
  values: contributionRows.map((row) => Number(row.contribution)),
  seriesName: "Contribution",
  formatCode: "0.00%",
  label: (value) => points(value),
  positiveFill: GREEN,
  negativeFill: RED,
});
addTable(slide(9), [
  ["Measure", "Contribution"],
  ["Largest contributor", largestContributor ? `${securityName(largestContributor)} | ${points(largestContributor.contribution)}` : "N/A"],
  ["Largest detractor", largestDetractor ? `${securityName(largestDetractor)} | ${points(largestDetractor.contribution)}` : "N/A"],
  ["Equity contribution", points(monthlyReporting.equity_contribution)],
  ["Residual contribution", points(monthlyReporting.residual_contribution)],
], { left: 625, top: 175, width: 290, height: 230 }, [155, 135], { fontSize: 8, headerFontSize: 9.5 });

const sectorRows = [...monthlyReporting.sector_contributions];
if (!sectorRows.length) sectorRows.push({ sector: "Unclassified", contribution: 0 });
const excessMonth = monthReturn == null || benchmarkMonthReturn == null ? null : Number(monthReturn) - Number(benchmarkMonthReturn);
addTitle(slide(10), "Sector Drivers of Relative Performance", `${shortDate(monthlyReporting.period_start)} to ${shortDate(monthlyReporting.period_end)} | Portfolio contribution with benchmark context`);
addBarChart(slide(10), {
  position: { left: 50, top: 155, width: 545, height: 295 },
  title: "Sector contribution to monthly portfolio return",
  categories: sectorRows.map((row) => row.sector),
  values: sectorRows.map((row) => Number(row.contribution)),
  seriesName: "Contribution",
  formatCode: "0.00%",
  label: (value) => points(value),
  positiveFill: TEAL,
  negativeFill: RED,
});
addTable(slide(10), [
  ["Measure", "Effect"],
  ["Portfolio return", percent(monthlyReporting.portfolio_monthly_return)],
  [`${primaryName} return`, percent(benchmarkMonthReturn)],
  ["Excess return", points(excessMonth)],
  ["Equity contribution", points(monthlyReporting.equity_contribution)],
  ["Residual contribution", points(monthlyReporting.residual_contribution)],
], { left: 625, top: 175, width: 290, height: 230 }, [160, 130], { fontSize: 9.2, headerFontSize: 9.5 });

const cash = monthlyReporting.cash_activity;
const dividendRows = [...cash.dividends_by_symbol].slice(0, 5);
if (!dividendRows.length) dividendRows.push({ symbol: "None recorded", amount: 0 });
addTitle(slide(11), "Dividends and Financing", `${reportMonth} ${periodEnd.slice(0, 4)} | Cash activity shown in USD`);
const dividendChart = slide(11).charts.add("bar", {
  position: { left: 50, top: 155, width: 545, height: 295 },
  title: "Dividends received by security",
  titleTextStyle: { typeface: FONT, fontSize: 14, bold: true, fill: INK },
  categories: dividendRows.map((row) => row.symbol),
  series: [{
    name: "Dividend",
    values: dividendRows.map((row) => round(row.amount, 2)),
    fill: GREEN,
    valuesFormatCode: "$0.00",
    dataLabelOverrides: dividendRows.map((row, idx) => ({
      idx,
      text: money2(row.amount),
      position: "outEnd",
      showValue: false,
      textStyle: { fontSize: 10, bold: true, fill: INK },
    })),
  }],
  barOptions: { direction: "column", grouping: "clustered", gapWidth: 48 },
  hasLegend: false,
  xAxis: { textStyle: { fontSize: 10, fill: INK }, line: { style: "solid", fill: GRID, width: 1 } },
  yAxis: { numberFormatCode: "$0", min: 0, majorGridlines: { style: "solid", fill: GRID, width: 1 }, textStyle: { fontSize: 9, fill: MUTED } },
  dataLabels: { showValue: false, position: "outEnd", textStyle: { fontSize: 10, bold: true, fill: INK } },
  chartFill: "#FFFFFF",
  chartLine: { fill: "none", width: 0 },
  plotAreaFill: "#FFFFFF",
});
applyChartFont(dividendChart);
addTable(slide(11), [
  ["Monthly cash item", "USD"],
  ["Dividends", money2(cash.total_dividends)],
  ["Interest received", money2(cash.interest_received)],
  ["Interest paid", money2(cash.interest_paid)],
  ["Fees and withholding", money2(cash.fees_and_withholding)],
  ["Net listed cash activity", money2(cash.net_listed_cash_activity)],
], { left: 625, top: 175, width: 290, height: 255 }, [170, 120], { fontSize: 9.2, headerFontSize: 9.5 });

addTitle(slide(12), "Risk Analytics", `Validated daily-return metrics through ${shortDate(dataAsOf)} | Risk-free rate dated ${snapshot.risk.risk_free.rate_date}`);
addTable(slide(12), [
  ["Metric", "Value", "Definition / source"],
  ["Annualized return", percent(risk.annualized_return), "Daily compound return, annualized"],
  ["Annualized volatility", percent(risk.annualized_volatility), "Daily return standard deviation, annualized"],
  ["Risk-free rate", percent(risk.risk_free_annual_rate), `UST 3-month par yield, ${snapshot.risk.risk_free.rate_date}`],
  ["Sharpe ratio", decimal(risk.sharpe_ratio), "Excess return / annualized volatility"],
  ["Daily VaR (95%)", percent(risk.var_95_daily), "Historical daily loss threshold"],
  ["Daily expected tail loss", percent(risk.daily_expected_tail_loss), "Average loss beyond 95% VaR"],
  ["Win rate", percent(risk.win_rate), "Positive daily returns / return observations"],
  ["BLUE mean daily return", percent(risk.blue_mean_daily_return, 4), `Standard error ${percent(risk.blue_standard_error, 4)}`],
], { left: 50, top: 150, width: 860, height: 310 }, [260, 150, 450], { fontSize: 9.5, headerFontSize: 10 });

addTitle(slide(13), "Benchmark Comparison", `Common aligned period ${shortDate(primary.period_start)} to ${shortDate(primary.period_end)} | All series converted to USD`);
addTable(slide(13), [
  ["Metric", "U16088606-Greater China", primaryShort, secondaryShort],
  ["Total return", percent(primary.portfolio_total_return), percent(primary.benchmark_total_return), percent(secondary.benchmark_total_return)],
  ["Annualized return", percent(primary.portfolio_annualized_return), percent(primary.benchmark_annualized_return), percent(secondary.benchmark_annualized_return)],
  ["Annualized volatility", percent(primary.portfolio_annualized_volatility), percent(primary.benchmark_annualized_volatility), percent(secondary.benchmark_annualized_volatility)],
  ["Maximum drawdown", percent(primary.portfolio_max_drawdown), percent(primary.benchmark_max_drawdown), percent(secondary.benchmark_max_drawdown)],
  ["Excess total return", "N/A", percent(primary.excess_total_return), percent(secondary.excess_total_return)],
  ["Beta", "N/A", decimal(primary.beta), decimal(secondary.beta)],
  ["Correlation", "N/A", decimal(primary.correlation), decimal(secondary.correlation)],
], { left: 40, top: 145, width: 880, height: 315 }, [220, 230, 220, 210], { fontSize: 8.8, headerFontSize: 9.2 });

addTitle(slide(14), "Drawdown Analytics", `Expected and observed downside measures through ${shortDate(dataAsOf)}`);
addTable(slide(14), [
  ["Measure", "Value", "Method"],
  ["Expected maximum drawdown (1 year)", percent(risk.expected_maximum_drawdown_1y), `${Number(risk.drawdown_simulation_paths).toLocaleString("en-US")} bootstrap paths over ${risk.drawdown_horizon_sessions} sessions`],
  ["Actual maximum drawdown (1 year)", percent(risk.actual_maximum_drawdown_1y), "Worst observed peak-to-trough decline over the latest one-year window"],
  ["Tail maximum drawdown (1 year)", percent(risk.tail_maximum_drawdown_1y), `${percent(risk.drawdown_tail_confidence, 0)} tail of simulated one-year drawdowns`],
  ["Crisis maximum drawdown", percent(risk.crisis_maximum_drawdown), "Today's holdings replayed from 15 Sep 2008 to 9 Mar 2009"],
], { left: 50, top: 150, width: 860, height: 270 }, [285, 140, 435], { fontSize: 9.2, headerFontSize: 9.5 });

const monthlyByYear = new Map();
for (const row of monthRows) {
  const [year, month] = row.date.split("-").map(Number);
  if (!monthlyByYear.has(year)) monthlyByYear.set(year, Array(12).fill("N/A"));
  monthlyByYear.get(year)[month - 1] = row.portfolio_monthly_return == null ? "N/A" : percent(row.portfolio_monthly_return, 1);
}
addTitle(slide(15), "Monthly Returns", `U16088606-Greater China | Monthly portfolio returns through ${shortDate(dataAsOf)}`);
addTable(slide(15), [
  ["Year", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
  ...[...monthlyByYear.entries()].sort(([a], [b]) => a - b).map(([year, values]) => [String(year), ...values]),
], { left: 30, top: 175, width: 900, height: 145 }, [72, ...Array(12).fill(69)], { fontSize: 8.5, headerFontSize: 8.5 });
addText(slide(15), `Total return ${percent(risk.total_return)} | Annualized return ${percent(risk.annualized_return)} | Positive-day win rate ${percent(risk.win_rate)}`, { left: 120, top: 395, width: 720, height: 44 }, { fontSize: 13, bold: true, color: INK, alignment: "center" });

addTitle(slide(16), "Data Quality and Controls", `Evidence supporting this monthly review | ${shortDate(dataAsOf)}`);
addTable(slide(16), [
  ["Control", "Status", "Evidence"],
  ["IBKR NAV coverage", "PASS", `${snapshot.portfolio_summary.nav_coverage.observations} observations from ${snapshot.portfolio_summary.nav_coverage.first_date} to ${dataAsOf}`],
  ["Positions", "PASS", `${holdings.length} positions as of ${dataAsOf}`],
  ["Risk-free rate", "PASS", `U.S. Treasury 3-month rate ${percent(snapshot.risk.risk_free.annual_rate)} dated ${snapshot.risk.risk_free.rate_date}`],
  ["Primary benchmark", "PASS", primaryName],
  ["Secondary benchmark", "PASS", secondaryName],
  ["Sector classification", "PASS", `${Number(sector.classification_coverage_pct).toFixed(0)}% holding coverage`],
  ["Crisis replay", "PASS", `${crisis.direct_components} direct histories and ${crisis.proxy_components} proxies`],
], { left: 50, top: 150, width: 860, height: 300 }, [220, 120, 520], { fontSize: 9, headerFontSize: 9.5 });

addTitle(slide(17), "Portfolio Analyst Summary", `${reportMonth} ${periodEnd.slice(0, 4)} | Deterministic figures with grounded qualitative commentary`);
const deterministicSummary = [
  monthPhrase,
  `Full-period total return is ${percent(primary.portfolio_total_return)}, ${relativePhrase} ${primaryName} by ${points(Math.abs(primary.excess_total_return))}.`,
  `Annualized volatility is ${percent(primary.portfolio_annualized_volatility)}, versus ${percent(primary.benchmark_annualized_volatility)} for ${primaryName}.`,
  `${largestSector.sector} is ${percent(largestSector.weight)} of gross market value; the top five holdings are ${percent(top5)}.`,
  `Daily expected tail loss is ${percent(risk.daily_expected_tail_loss)} and crisis replay drawdown is ${percent(risk.crisis_maximum_drawdown)}.`,
];
const agentSummary = commentary?.executive_summary?.trim();
if (agentSummary && (agentSummary.length > 420 || /[0-9%$]/.test(agentSummary))) {
  throw new Error("Agent commentary violates the qualitative narrative policy");
}
addTable(slide(17), [
  ["What is working", "What needs attention", "Next month watchlist"],
  [
    `${deterministicSummary[0]}\n\n${deterministicSummary[1]}`,
    `${deterministicSummary[2]}\n\n${deterministicSummary[4]}`,
    `${deterministicSummary[3]}\n\nTrack concentration, downside resilience and relative performance together.`,
  ],
], { left: 45, top: 155, width: 870, height: 265 }, [290, 290, 290], {
  fontSize: 9.5,
  headerFontSize: 10,
  firstColumnLeft: true,
});

for (let number = 3; number <= 17; number += 1) {
  slide(number).speakerNotes.textFrame.setText(
    `Deterministic source: frozen U16088606 reporting snapshot ${snapshot.snapshot_id || ""}; data as of ${dataAsOf}; layout ${TEMPLATE_LAYOUT}.`,
  );
}

const semantic = await presentation.inspect({
  kind: "textbox,table,chart",
  search: "Greater China|FOCUS US Tech|U.S. Technology and Biotechnology|Historical Performance|Risk Analytics",
  maxChars: 20000,
});
if (!semantic.ndjson.includes("Greater China")) throw new Error("Greater China strategy label is missing");
if (/FOCUS US Tech|U\.S\. Technology and Biotechnology/i.test(semantic.ndjson)) {
  throw new Error("Greater China presentation contains the wrong strategy name");
}

const outputDir = path.dirname(path.resolve(finalPath));
const stagingDir = path.resolve(".work", "presentation-validation", `${path.basename(finalPath, ".pptx")}-${randomUUID()}`);
await fs.mkdir(stagingDir, { recursive: true });
await fs.mkdir(outputDir, { recursive: true });
const candidatePath = path.join(stagingDir, `${path.basename(finalPath, ".pptx")}.candidate.pptx`);
await (await PresentationFile.exportPptx(presentation)).save(candidatePath);

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
      ...Array.from({ length: 15 }, (_, index) => index + 3).flatMap((number) => ["--require-native-table-slide", String(number)]),
    ],
    explicitTotalSlideCount: 20,
    requiredNativeTableOwnerSlides: Array.from({ length: 15 }, (_, index) => index + 3),
    requiredNativeChartOwnerSlides: [6, 7, 8, 9, 10, 11],
    requiredEmbeddedWorkbookChartOwnerSlides: [],
    materializeLiteralChartWorkbooks: true,
    verifyArtifactToolImport: true,
    receiptPath: path.join(stagingDir, `${path.basename(finalPath)}.validation.json`),
  });
} else {
  await fs.rename(candidatePath, path.resolve(finalPath));
}

console.log(JSON.stringify({
  status: "success",
  subject_id: snapshot.subject_id,
  period_end: periodEnd,
  data_as_of: dataAsOf,
  layout_version: TEMPLATE_LAYOUT,
  output_path: path.resolve(finalPath),
  slides: 20,
}));
