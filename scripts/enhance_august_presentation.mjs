import fs from "node:fs/promises";
import path from "node:path";
import { createHash, randomUUID } from "node:crypto";
import { pathToFileURL } from "node:url";
import { FileBlob, PresentationFile } from "@oai/artifact-tool";


const [sourcePath, snapshotPath, finalPath] = process.argv.slice(2);
if (!sourcePath || !snapshotPath || !finalPath) {
  throw new Error("Usage: enhance_august_presentation.mjs source.pptx snapshot.json output.pptx");
}

const sourceBytes = await fs.readFile(sourcePath);
const sourceSha256 = createHash("sha256").update(sourceBytes).digest("hex");
const snapshot = JSON.parse(await fs.readFile(snapshotPath, "utf8"));
if (snapshot.subject_id !== "U16088571" || snapshot.period_end !== "2026-08-31") {
  throw new Error("The enhanced deck requires the frozen U16088571 August 2026 snapshot");
}
const presentation = await PresentationFile.importPptx(await FileBlob.load(sourcePath));

const FONT = "Arial";
const INK = "#151B26";
const MUTED = "#5D6878";
const GRID = "#D9E0E8";
const GREEN = "#00884A";
const MINT = "#54BFAF";
const RED = "#C94F52";
const TEMPLATE_BLUE = "#4D69B8";
const TEMPLATE_TEAL = "#45C3B7";
const NAVY = "#111827";
const PALE_GREEN = "#E9F7F2";

const round = (value, digits = 6) => Number(Number(value).toFixed(digits));
const normalizedAxis = (values, step = 10) => {
  const clean = values.map(Number).filter(Number.isFinite);
  const min = Math.floor(Math.min(...clean) / step) * step;
  const maxCandidate = Math.ceil(Math.max(...clean) / step) * step;
  return maxCandidate > min
    ? { min, max: maxCandidate, majorUnit: step }
    : { min: min - step, max: maxCandidate + step, majorUnit: step };
};

const addText = (slide, text, position, options = {}) => {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position,
    fill: "none",
    line: { fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = {
    typeface: FONT,
    fontSize: options.fontSize ?? 14,
    bold: options.bold ?? false,
    color: options.color ?? INK,
    alignment: options.alignment ?? "left",
    autoFit: "none",
  };
  return shape;
};

const styleTable = (table, { headerFill = NAVY, fontSize = 11, headerFontSize = 11 } = {}) => {
  table.borders.assign({ style: "solid", fill: GRID, width: 1 });
  for (let row = 0; row < table.rows.length; row += 1) {
    for (let column = 0; column < table.columns.length; column += 1) {
      const cell = table.getCell(row, column);
      const isHeader = row === 0;
      const value = cell.text.toString();
      cell.fill = isHeader ? headerFill : "#FFFFFF";
      cell.value = {
        runs: [{
          run: value,
          textStyle: {
            typeface: FONT,
            fontSize: `${isHeader ? headerFontSize : fontSize}px`,
            bold: isHeader,
            color: isHeader ? "#FFFFFF" : INK,
          },
        }],
        paragraphStyle: { alignment: column === 0 ? "left" : "right" },
      };
      cell.margins = { left: 6, right: 6, top: 3, bottom: 3 };
      cell.anchor = "middle";
      cell.horizontalOverflow = "clip";
    }
  }
  return table;
};

const applyChartFont = (chart) => {
  const styles = [
    chart.titleTextStyle,
    chart.legend?.textStyle,
    chart.dataLabels?.textStyle,
    chart.xAxis?.textStyle,
    chart.yAxis?.textStyle,
  ];
  for (const style of styles) if (style) style.typeface = FONT;
  return chart;
};

const replaceChart = (slide, chartType, config) => {
  for (const chart of [...slide.charts.items]) slide.charts.deleteById(chart.id);
  return applyChartFont(slide.charts.add(chartType, config));
};

const prepareDuplicate = (sourceSlide, targetIndex, headerText) => {
  const slide = sourceSlide.duplicate();
  slide.moveTo(targetIndex);
  for (const chart of [...slide.charts.items]) slide.charts.deleteById(chart.id);
  for (const image of [...slide.images.items]) slide.images.deleteById(image.id);
  for (const shape of [...slide.shapes.items]) {
    const numericId = Number(shape.id);
    if (Number.isFinite(numericId) && numericId >= 22) slide.shapes.deleteById(shape.id);
  }
  slide.shapes.getById("6").text.set(headerText);
  slide.shapes.getById("7").text.set("iStrategy: FOCUS US Tech & Biotech Strategy");
  slide.shapes.getById("17").text.set(" Results ");
  return slide;
};

const setSummaryTable = (slide, values) => {
  const table = slide.tables.items[0];
  if (!table || table.rowCount !== 6 || table.columnCount !== 2) {
    throw new Error("Expected the approved six-row summary table");
  }
  for (let row = 0; row < 6; row += 1) {
    for (let column = 0; column < 2; column += 1) {
      table.cells.set(row, column, String(values[row][column]));
    }
  }
  return table;
};

const addSlideTitle = (slide, title, subtitle) => {
  addText(slide, title, { left: 19.8, top: 114.5, width: 560, height: 26 }, {
    fontSize: 20,
    bold: true,
  });
  addText(slide, subtitle, { left: 20.2, top: 140.5, width: 760, height: 18 }, {
    fontSize: 10.5,
    color: MUTED,
  });
};

// Rebuild the three existing quantitative charts from the frozen snapshot so
// every chart in the revised deck has a complete literal workbook snapshot.
const sector = snapshot.sector_allocation;
const holdings = [...snapshot.holdings.holdings]
  .sort((a, b) => Math.abs(Number(b.market_value_text)) - Math.abs(Number(a.market_value_text)));
const grossMarketValue = Number(sector.gross_market_value);
const weights = holdings.map((row) => Math.abs(Number(row.market_value_text)) / grossMarketValue);
replaceChart(presentation.slides.getItem(5), "bar", {
  position: { left: 52.5, top: 142.5, width: 540, height: 292.5 },
  title: "Top 10 holdings - % of gross market value",
  titleTextStyle: { typeface: FONT, fontSize: 15, bold: true, fill: INK },
  categories: holdings.slice(0, 10).map((row) => row.symbol),
  series: [{
    name: "Weight",
    values: weights.slice(0, 10).map((value) => round(value)),
    fill: TEMPLATE_BLUE,
    valuesFormatCode: "0.0%",
  }],
  barOptions: { direction: "bar", grouping: "clustered", gapWidth: 44 },
  hasLegend: false,
  xAxis: {
    numberFormatCode: "0%",
    min: 0,
    max: 0.7,
    majorUnit: 0.1,
    majorGridlines: { style: "solid", fill: GRID, width: 1 },
    textStyle: { fontSize: 11, fill: MUTED },
  },
  yAxis: {
    textStyle: { fontSize: 12, fill: INK },
    line: { style: "solid", fill: GRID, width: 1 },
  },
  dataLabels: {
    showValue: true,
    position: "outEnd",
    textStyle: { fontSize: 11, bold: true, fill: INK },
  },
  chartFill: "#FFFFFF",
  chartLine: { fill: "none", width: 0 },
  plotAreaFill: "#FFFFFF",
});

const monthRows = snapshot.month_end_performance;
const chartCategories = monthRows.map((row) => {
  const [year, month] = row.date.split("-").map(Number);
  return `${new Intl.DateTimeFormat("en-GB", { month: "short", timeZone: "UTC" })
    .format(new Date(Date.UTC(year, month - 1, 1)))}-${String(year).slice(2)}`;
});
const performanceAxis = normalizedAxis(monthRows.flatMap((row) => [row.portfolio, row.benchmark]));
replaceChart(presentation.slides.getItem(6), "line", {
  position: { left: 52.5, top: 141, width: 810, height: 270 },
  title: "Rebased performance comparison",
  titleTextStyle: { typeface: FONT, fontSize: 15, bold: true, fill: INK },
  categories: chartCategories,
  series: [
    {
      name: "U16088571",
      values: monthRows.map((row) => round(row.portfolio, 4)),
      line: { style: "solid", fill: TEMPLATE_TEAL, width: 4 },
    },
    {
      name: "S&P 500 Price Index",
      values: monthRows.map((row) => round(row.benchmark, 4)),
      line: { style: "solid", fill: NAVY, width: 3 },
    },
  ],
  lineOptions: { smooth: true },
  hasLegend: true,
  legend: { position: "bottom", overlay: false, textStyle: { fontSize: 11, fill: INK } },
  xAxis: {
    title: { text: "Month-end", textStyle: { typeface: FONT, fontSize: 11, fill: MUTED } },
    textStyle: { fontSize: 9, fill: MUTED },
    line: { style: "solid", fill: GRID, width: 1 },
  },
  yAxis: {
    title: { text: "Rebased index", textStyle: { typeface: FONT, fontSize: 11, fill: MUTED } },
    numberFormatCode: "0",
    min: 0,
    max: 140,
    majorUnit: 20,
    majorGridlines: { style: "solid", fill: GRID, width: 1 },
    textStyle: { fontSize: 10, fill: MUTED },
  },
  chartFill: "#FFFFFF",
  chartLine: { fill: "none", width: 0 },
  plotAreaFill: "#FFFFFF",
});

replaceChart(presentation.slides.getItem(7), "bar", {
  position: { left: 52.5, top: 142.5, width: 570, height: 292.5 },
  title: "Sector weights",
  titleTextStyle: { typeface: FONT, fontSize: 15, bold: true, fill: INK },
  categories: sector.allocation.map((row) => row.sector),
  series: [{
    name: "Weight",
    values: sector.allocation.map((row) => round(row.weight)),
    fill: TEMPLATE_TEAL,
    valuesFormatCode: "0.00%",
    dataLabelOverrides: sector.allocation.map((row, idx) => ({
      idx,
      text: `${(Number(row.weight) * 100).toFixed(2)}%`,
      position: "outEnd",
      showValue: false,
      textStyle: { fontSize: 11, bold: true, fill: INK },
    })),
  }],
  barOptions: { direction: "bar", grouping: "clustered", gapWidth: 48 },
  hasLegend: false,
  xAxis: {
    numberFormatCode: "0%",
    min: 0,
    majorGridlines: { style: "solid", fill: GRID, width: 1 },
    textStyle: { fontSize: 11, fill: MUTED },
  },
  yAxis: {
    textStyle: { fontSize: 11, fill: INK },
    line: { style: "solid", fill: GRID, width: 1 },
  },
  dataLabels: {
    showValue: false,
    position: "outEnd",
    textStyle: { fontSize: 11, bold: true, fill: INK },
  },
  chartFill: "#FFFFFF",
  chartLine: { fill: "none", width: 0 },
  plotAreaFill: "#FFFFFF",
});

const sourceSlide = presentation.slides.getItem(5);
const contributionSlide = prepareDuplicate(
  sourceSlide,
  8,
  "Holdings, allocations and performance",
);
const attributionSlide = prepareDuplicate(
  sourceSlide,
  9,
  "Holdings, allocations and performance",
);
const incomeSlide = prepareDuplicate(
  sourceSlide,
  10,
  "Holdings, allocations and performance",
);

// Slide 9: security-level monthly contribution.
addSlideTitle(
  contributionSlide,
  "Monthly Security Contribution",
  "Time-weighted contribution to return | 3 August 2026 to 31 August 2026",
);
const contributionLabels = ["HL", "MU", "CDE", "ORCL", "PSKY", "LMT", "TTWO", "VSAT", "WDC", "BKD"];
const contributionValues = [0.0152, 0.0145, 0.0093, 0.0071, 0.0053, -0.0012, -0.0013, -0.0035, -0.0061, -0.0072];
const contributionChart = contributionSlide.charts.add("bar", {
  position: { left: 52.5, top: 165, width: 540, height: 270 },
  title: "Top five contributors and bottom five detractors",
  titleTextStyle: { typeface: FONT, fontSize: 15, bold: true, fill: INK },
  categories: contributionLabels,
  series: [{
    name: "Contribution",
    values: contributionValues.map(Math.abs),
    fill: GREEN,
    valuesFormatCode: "0.00%",
    points: contributionValues.map((value, idx) => ({
      idx,
      fill: value >= 0 ? GREEN : RED,
      line: { fill: value >= 0 ? GREEN : RED, width: 1 },
    })),
    dataLabelOverrides: contributionValues.map((value, idx) => ({
      idx,
      text: `${(value * 100).toFixed(2)}%`,
      position: "outEnd",
      showValue: false,
      textStyle: { fontSize: 10.5, bold: true, fill: INK },
    })),
  }],
  barOptions: { direction: "bar", grouping: "clustered", gapWidth: 45, varyColors: true },
  hasLegend: false,
  xAxis: {
    numberFormatCode: "0.0%",
    min: 0,
    max: 0.016,
    majorUnit: 0.005,
    majorGridlines: { style: "solid", fill: GRID, width: 1 },
    textStyle: { fontSize: 10.5, fill: MUTED },
    line: { style: "solid", fill: GRID, width: 1 },
  },
  yAxis: {
    textStyle: { fontSize: 11.5, fill: INK },
    line: { style: "solid", fill: GRID, width: 1 },
  },
  dataLabels: {
    showValue: false,
    position: "outEnd",
    textStyle: { fontSize: 10.5, bold: true, fill: INK },
  },
  chartFill: "#FFFFFF",
  chartLine: { fill: "none", width: 0 },
  plotAreaFill: "#FFFFFF",
});
applyChartFont(contributionChart);
setSummaryTable(contributionSlide, [
  ["Measure", "Contribution"],
  ["Largest contributor: HL", "1.52 pp"],
  ["Largest detractor: BKD", "-0.72 pp"],
  ["Equity contribution", "5.59 pp"],
  ["Cash contribution", "-0.02 pp"],
  ["Portfolio return", "5.57%"],
]);
addText(
  contributionSlide,
  "Security contribution measures each position's effect on the portfolio's monthly return. It differs from ending market value and unrealized profit or loss.",
  { left: 626.25, top: 390, width: 236.25, height: 68 },
  { fontSize: 10.5, color: MUTED },
);
addText(
  contributionSlide,
  "Source: IBKR PortfolioAnalyst report, pages 22-23. Figures use the report's monthly TWR methodology.",
  { left: 52.5, top: 466, width: 810, height: 20 },
  { fontSize: 9.5, color: MUTED },
);
contributionSlide.speakerNotes.textFrame.setText(
  "Source: uploaded IBKR Presentation deck.pdf, PortfolioAnalyst pages 22-23. Analysis period 3 August 2026 to 31 August 2026. Values are security contributions to monthly time-weighted return.",
);

// Slide 10: sector attribution relative to S&P 500.
addSlideTitle(
  attributionSlide,
  "Sector Attribution Versus S&P 500",
  "Allocation and security-selection effects | 3 August 2026 to 31 August 2026",
);
const sectorLabels = [
  "Consumer Cyclicals",
  "Industrial",
  "Basic Materials",
  "Utilities",
  "Financials",
  "Telecom",
  "Energy",
  "Healthcare",
  "Technology",
];
const sectorEffects = [0.0048, 0.0040, 0.0034, 0.0014, 0.0007, -0.0003, -0.0022, -0.0022, -0.0074];
const attributionChart = attributionSlide.charts.add("bar", {
  position: { left: 52.5, top: 165, width: 540, height: 270 },
  title: "Total attribution effect by sector - green positive, red negative",
  titleTextStyle: { typeface: FONT, fontSize: 15, bold: true, fill: INK },
  categories: sectorLabels,
  series: [{
    name: "Total effect",
    values: sectorEffects.map(Math.abs),
    fill: MINT,
    valuesFormatCode: "0.00%",
    points: sectorEffects.map((value, idx) => ({
      idx,
      fill: value >= 0 ? MINT : RED,
      line: { fill: value >= 0 ? MINT : RED, width: 1 },
    })),
    dataLabelOverrides: sectorEffects.map((value, idx) => ({
      idx,
      text: `${(value * 100).toFixed(2)}%`,
      position: "outEnd",
      showValue: false,
      textStyle: { fontSize: 10, bold: true, fill: INK },
    })),
  }],
  barOptions: { direction: "bar", grouping: "clustered", gapWidth: 38, varyColors: true },
  hasLegend: false,
  xAxis: {
    numberFormatCode: "0.0%",
    min: 0,
    max: 0.008,
    majorUnit: 0.002,
    majorGridlines: { style: "solid", fill: GRID, width: 1 },
    textStyle: { fontSize: 10, fill: MUTED },
    line: { style: "solid", fill: GRID, width: 1 },
  },
  yAxis: {
    textStyle: { fontSize: 10.5, fill: INK },
    line: { style: "solid", fill: GRID, width: 1 },
  },
  dataLabels: {
    showValue: false,
    position: "outEnd",
    textStyle: { fontSize: 10, bold: true, fill: INK },
  },
  chartFill: "#FFFFFF",
  chartLine: { fill: "none", width: 0 },
  plotAreaFill: "#FFFFFF",
});
applyChartFont(attributionChart);
setSummaryTable(attributionSlide, [
  ["Measure", "Effect"],
  ["Portfolio return", "5.57%"],
  ["S&P 500 return", "2.72%"],
  ["Allocation effect", "2.44 pp"],
  ["Selection effect", "0.41 pp"],
  ["Total attribution", "2.85 pp"],
]);
addText(
  attributionSlide,
  "Technology allocation added 1.34 pp, but security selection subtracted 2.08 pp, leaving a -0.74 pp total technology effect. Consumer cyclicals, industrials and basic materials supplied the largest positive sector effects.",
  { left: 626.25, top: 390, width: 236.25, height: 76 },
  { fontSize: 9.8, color: MUTED },
);
addText(
  attributionSlide,
  "Source: IBKR PortfolioAnalyst report, pages 18-19. Attribution uses the report's Frongello smoothing method.",
  { left: 52.5, top: 466, width: 810, height: 20 },
  { fontSize: 9.5, color: MUTED },
);
attributionSlide.speakerNotes.textFrame.setText(
  "Source: uploaded IBKR Presentation deck.pdf, PortfolioAnalyst pages 18-19 and page 31. Monthly attribution uses the Frongello method stated in the report disclosure.",
);

// Slide 11: dividends and financing cost.
addSlideTitle(
  incomeSlide,
  "Dividends and Financing",
  "Cash income and debit interest recorded during August 2026",
);
const dividendChart = incomeSlide.charts.add("bar", {
  position: { left: 52.5, top: 165, width: 540, height: 270 },
  title: "Dividends received by security",
  titleTextStyle: { typeface: FONT, fontSize: 15, bold: true, fill: INK },
  categories: ["AAPL", "ABBV", "VRSN"],
  series: [{
    name: "Dividend amount",
    values: [17.28, 1.73, 11.34],
    fill: GREEN,
    valuesFormatCode: "$0.00",
  }],
  barOptions: { direction: "column", grouping: "clustered", gapWidth: 70 },
  hasLegend: false,
  xAxis: {
    textStyle: { fontSize: 11.5, fill: INK },
    line: { style: "solid", fill: GRID, width: 1 },
  },
  yAxis: {
    numberFormatCode: "$0",
    min: 0,
    max: 20,
    majorUnit: 5,
    majorGridlines: { style: "solid", fill: GRID, width: 1 },
    textStyle: { fontSize: 10.5, fill: MUTED },
  },
  dataLabels: {
    showValue: true,
    position: "outEnd",
    textStyle: { fontSize: 11, bold: true, fill: INK },
  },
  chartFill: "#FFFFFF",
  chartLine: { fill: "none", width: 0 },
  plotAreaFill: "#FFFFFF",
});
applyChartFont(dividendChart);
setSummaryTable(incomeSlide, [
  ["Monthly cash item", "USD"],
  ["AAPL dividend", "$17.28"],
  ["ABBV dividend", "$1.73"],
  ["VRSN dividend", "$11.34"],
  ["Debit interest", "-$22.32"],
  ["Net after interest", "$8.03"],
]);
addText(
  incomeSlide,
  "Dividend receipts exceeded the month's recorded debit interest by $8.03. These cash items form part of account NAV, but they are not the same as security-level return contribution.",
  { left: 626.25, top: 390, width: 236.25, height: 68 },
  { fontSize: 10.5, color: MUTED },
);
addText(
  incomeSlide,
  "Source: IBKR PortfolioAnalyst report, pages 29-30. Dividends: AAPL paid 13 Aug, ABBV 14 Aug and VRSN 27 Aug 2026. Amounts are shown in USD.",
  { left: 52.5, top: 466, width: 810, height: 18 },
  { fontSize: 9.5, color: MUTED },
);
incomeSlide.speakerNotes.textFrame.setText(
  "Source: uploaded IBKR Presentation deck.pdf, PortfolioAnalyst pages 29-30. The interest source line in the report reads USD Debit Interest Paid for July 2026; this slide shortens the description but preserves the amount.",
);

const workspaceDir = path.resolve(".");
const skillDir = process.env.SKILL_DIR;
const runtimePython = process.env.RUNTIME_PYTHON;
if (!skillDir || !runtimePython || !process.env.RUNTIME_NODE_MODULES) {
  throw new Error("SKILL_DIR, RUNTIME_PYTHON and RUNTIME_NODE_MODULES are required");
}
const stagingDir = path.resolve(
  ".work",
  "presentation-validation",
  `${path.basename(finalPath, ".pptx")}-${randomUUID()}`,
);
await fs.mkdir(stagingDir, { recursive: true });
await fs.mkdir(path.dirname(path.resolve(finalPath)), { recursive: true });
const candidatePath = path.join(stagingDir, `${path.basename(finalPath, ".pptx")}.candidate.pptx`);
await (await PresentationFile.exportPptx(presentation)).save(candidatePath);

const { finalizePresentation } = await import(pathToFileURL(
  path.join(skillDir, "container_tools/artifact_tool_utils.mjs"),
).href);

await finalizePresentation({
  workspaceDir,
  candidatePath,
  finalPath: path.resolve(finalPath),
  pythonExecutable: runtimePython,
  integrityValidatorPath: path.join(skillDir, "container_tools/inspect_presentation_package_integrity.py"),
  layoutValidatorPath: path.join(skillDir, "container_tools/inspect_presentation_layout_geometry.py"),
  layoutArgs: [
    "--expected-slide-size-emu", "9144000,5143500",
    "--validate-bullet-geometry",
    "--validate-heading-fit",
    ...[3, 5, 6, 9, 10, 11, 12, 13, 14, 15, 16].flatMap((number) => [
      "--require-native-table-slide", String(number),
    ]),
  ],
  explicitTotalSlideCount: 20,
  requiredNativeTableOwnerSlides: [3, 5, 6, 9, 10, 11, 12, 13, 14, 15, 16],
  requiredNativeChartOwnerSlides: [6, 7, 8, 9, 10, 11],
  requiredEmbeddedWorkbookChartOwnerSlides: [],
  materializeLiteralChartWorkbooks: true,
  verifyArtifactToolImport: true,
  fontPolicy: {
    basis: "reference",
    families: ["Arial", "Aptos Display", "Georgia"],
    referencePath: path.resolve(sourcePath),
    referenceSha256: sourceSha256,
  },
  receiptPath: path.join(stagingDir, `${path.basename(finalPath)}.validation.json`),
});

console.log(JSON.stringify({
  status: "success",
  source_sha256: sourceSha256,
  output_path: path.resolve(finalPath),
  slides: 20,
  added_slides: [
    "Monthly Security Contribution",
    "Sector Attribution Versus S&P 500",
    "Dividends and Financing",
  ],
}));
