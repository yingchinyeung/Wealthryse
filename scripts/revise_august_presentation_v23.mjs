import fs from "node:fs/promises";
import path from "node:path";
import { createHash, randomUUID } from "node:crypto";
import { pathToFileURL } from "node:url";
import JSZip from "jszip";
import { FileBlob, PresentationFile } from "@oai/artifact-tool";

const [sourcePath, finalPath] = process.argv.slice(2);
if (!sourcePath || !finalPath) {
  throw new Error("Usage: revise_august_presentation_v23.mjs source.pptx output.pptx");
}

const workspaceDir = path.resolve(".");
const skillDir = process.env.SKILL_DIR;
const runtimePython = process.env.RUNTIME_PYTHON;
if (!skillDir || !runtimePython || !process.env.RUNTIME_NODE_MODULES) {
  throw new Error("SKILL_DIR, RUNTIME_PYTHON and RUNTIME_NODE_MODULES are required");
}

const sourceBytes = await fs.readFile(sourcePath);
const sourceSha256 = createHash("sha256").update(sourceBytes).digest("hex");
const presentation = await PresentationFile.importPptx(await FileBlob.load(sourcePath));

presentation.resolve("sh/0nut8rul").text.set("Sector Drivers of Relative Performance");

for (const [slideIndex, anchorId] of [
  [8, "sh/pofy50nu"],
  [9, "sh/dwjqtcbu"],
  [10, "sh/98jedc3u"],
]) {
  const shape = presentation.resolve(anchorId);
  presentation.slides.getItem(slideIndex).shapes.deleteById(shape.id);
}

presentation.resolve("th/{83F1C613-1D0F-40DE-B180-001B8EB382D7}").delete();

const semanticCheck = await presentation.inspect({
  kind: "textbox,thread",
  search: "Sector Drivers of Relative Performance|Source: IBKR PortfolioAnalyst|@Chloe Yoo",
  maxChars: 12000,
});
if (!semanticCheck.ndjson.includes("Sector Drivers of Relative Performance")) {
  throw new Error("Artifact Tool semantic edit did not apply the replacement title");
}

const stagingDir = path.resolve(
  ".work",
  "presentation-validation",
  `${path.basename(finalPath, ".pptx")}-${randomUUID()}`,
);
await fs.mkdir(stagingDir, { recursive: true });
await fs.mkdir(path.dirname(path.resolve(finalPath)), { recursive: true });
const candidatePath = path.join(stagingDir, `${path.basename(finalPath, ".pptx")}.candidate.pptx`);

// Apply the same small edits directly to the original OOXML package so that
// the approved deck's embedded chart workbooks remain byte-for-byte intact.
const zip = await JSZip.loadAsync(sourceBytes);
const readXml = async (name) => {
  const entry = zip.file(name);
  if (!entry) throw new Error(`Missing OOXML part: ${name}`);
  return entry.async("string");
};
const writeXml = (name, xml) => zip.file(name, xml);

const titlePart = "ppt/slides/slide10.xml";
let titleXml = await readXml(titlePart);
const oldTitle = "Sector Attribution Versus S&amp;P 500";
const newTitle = "Sector Drivers of Relative Performance";
if ((titleXml.match(new RegExp(oldTitle, "g")) ?? []).length !== 1) {
  throw new Error("Expected exactly one page 10 title match");
}
writeXml(titlePart, titleXml.replace(oldTitle, newTitle));

const visibleSourceLines = new Map([
  ["ppt/slides/slide9.xml", "Source: IBKR PortfolioAnalyst report, pages 22-23. Figures use the report's monthly TWR methodology."],
  ["ppt/slides/slide10.xml", "Source: IBKR PortfolioAnalyst report, pages 18-19. Attribution uses the report's Frongello smoothing method."],
  ["ppt/slides/slide11.xml", "Source: IBKR PortfolioAnalyst report, pages 29-30. Dividends: AAPL paid 13 Aug, ABBV 14 Aug and VRSN 27 Aug 2026. Amounts are shown in USD."],
]);
for (const [partName, sourceLine] of visibleSourceLines) {
  let xml = await readXml(partName);
  let removed = 0;
  xml = xml.replace(/<p:sp>(?:(?!<p:sp>)[\s\S])*?<\/p:sp>/g, (shapeXml) => {
    if (!shapeXml.includes(sourceLine)) return shapeXml;
    removed += 1;
    return "";
  });
  if (removed !== 1) throw new Error(`Expected one visible source line in ${partName}; removed ${removed}`);
  writeXml(partName, xml);
}

let slide18Xml = await readXml("ppt/slides/slide18.xml");
slide18Xml = slide18Xml.replace(
  /<p:extLst>\s*<p:ext uri="\{6950BFC3-D8DA-4A85-94F7-54DA5524770B\}">[\s\S]*?<\/p:ext>\s*<\/p:extLst>/,
  "",
);
writeXml("ppt/slides/slide18.xml", slide18Xml);

let slide18Rels = await readXml("ppt/slides/_rels/slide18.xml.rels");
slide18Rels = slide18Rels.replace(
  /<Relationship[^>]+Type="http:\/\/schemas\.microsoft\.com\/office\/2018\/10\/relationships\/comments"[^>]*\/>/,
  "",
);
writeXml("ppt/slides/_rels/slide18.xml.rels", slide18Rels);

let presentationRels = await readXml("ppt/_rels/presentation.xml.rels");
presentationRels = presentationRels.replace(
  /<Relationship[^>]+Type="http:\/\/schemas\.microsoft\.com\/office\/2018\/10\/relationships\/authors"[^>]*\/>/,
  "",
);
writeXml("ppt/_rels/presentation.xml.rels", presentationRels);

let contentTypes = await readXml("[Content_Types].xml");
contentTypes = contentTypes
  .replace(/<Override PartName="\/ppt\/authors\.xml"[^>]*\/>/, "")
  .replace(/<Override PartName="\/ppt\/comments\/modernComment\.xml"[^>]*\/>/, "");
writeXml("[Content_Types].xml", contentTypes);
zip.remove("ppt/authors.xml");
zip.remove("ppt/comments/modernComment.xml");

await fs.writeFile(candidatePath, await zip.generateAsync({
  type: "nodebuffer",
  compression: "DEFLATE",
  compressionOptions: { level: 6 },
}));

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
  title: "Sector Drivers of Relative Performance",
  removed_visible_sources: [9, 10, 11],
  removed_comment_threads: 1,
}));
