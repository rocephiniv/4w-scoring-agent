// Local workbook authoring only. No clinical scoring or source-workbook writes.
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";

const [runtimeRoot, planPath, outputDir, previewFlag] = process.argv.slice(2);
const fail = (code) => { throw new Error(code); };
const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");

try {
  if (!runtimeRoot || !planPath || !outputDir) fail("arguments_required");
  const original = await fs.readFile(planPath);
  const plan = JSON.parse(original.toString("utf8"));
  if (plan.version !== 1 || !Array.isArray(plan.sheets) || plan.sheets.length === 0) fail("invalid_workbook_plan");
  const names = new Set();
  for (const sheet of plan.sheets) {
    if (typeof sheet.name !== "string" || !sheet.name || sheet.name.length > 31 || /[\\/?*\[\]:]/.test(sheet.name) || names.has(sheet.name.toLowerCase())) fail("invalid_sheet_name");
    names.add(sheet.name.toLowerCase());
    if (!Array.isArray(sheet.headers) || !sheet.headers.length || sheet.headers.length > 16384 || !sheet.headers.every(h => typeof h === "string" && h.length > 0) || new Set(sheet.headers).size !== sheet.headers.length) fail("invalid_headers");
    if (!Array.isArray(sheet.rows) || sheet.rows.length > 1048575) fail("invalid_rows");
    if (!Array.isArray(sheet.widths) || sheet.widths.length !== sheet.headers.length || sheet.widths.some(w => !Number.isFinite(w) || w < 8 || w > 100)) fail("invalid_widths");
    for (const row of [sheet.headers, ...sheet.rows]) {
      if (!Array.isArray(row) || row.length !== sheet.headers.length) fail("invalid_row_shape");
      for (const value of row) {
        if (value !== null && !["string", "number", "boolean"].includes(typeof value)) fail("invalid_cell_type");
        if (typeof value === "number" && !Number.isFinite(value)) fail("invalid_number");
        if (typeof value === "string" && value.includes("\r")) fail("unsupported_carriage_return");
        if (typeof value === "string" && (value.length > 32767 || /[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(value))) fail("unsupported_cell_text");
      }
    }
  }
  // Resolve the installed approved package through a task-local dependency link.
  // No package internals or repository-local dependencies are used.
  const require = createRequire(path.join(path.resolve(runtimeRoot), "loader.cjs"));
  const { Workbook, SpreadsheetFile } = await import(pathToFileURL(require.resolve("@oai/artifact-tool")).href);
  await fs.mkdir(outputDir); // exclusive new directory; existing output is never replaced
  const workbook = Workbook.create();
  const literal = value => typeof value === "string" && /^=/.test(value) ? "'" + value : value;
  for (const [sheetIndex, spec] of plan.sheets.entries()) {
    const sheet = workbook.worksheets.add(spec.name);
    const rows = [spec.headers, ...spec.rows].map(row => row.map(literal));
    const area = sheet.getRangeByIndexes(0, 0, rows.length, spec.headers.length);
    area.values = rows;
    area.format.font = { name: "Arial", size: 11, color: "#202B36" };
    area.format.verticalAlignment = "top";
    area.format.wrapText = true;
    sheet.showGridLines = false;
    spec.widths.forEach((width, index) => { sheet.getRangeByIndexes(0, index, rows.length, 1).format.columnWidth = width; });
    const header = sheet.getRangeByIndexes(0, 0, 1, spec.headers.length);
    header.format.fill = "#223B53";
    header.format.font = { name: "Arial", size: 11, bold: true, color: "#FFFFFF" };
    header.format.horizontalAlignment = "center";
    area.format.autofitRows();
    sheet.freezePanes.freezeRows(1);
    if (spec.headers.length > 6) sheet.freezePanes.freezeColumns(1);
    if (spec.rows.length) sheet.tables.add(`A1:${columnName(spec.headers.length)}${rows.length}`, true, `Records${sheetIndex + 1}`);
  }
  workbook.recalculate();
  // Inspection is retained in the restricted output only when synthetic preview
  // is explicitly requested. Never print case text into general process logs.
  if (previewFlag === "--synthetic-preview") {
    const inspection = await workbook.inspect({ kind: "workbook,sheet,table", maxChars: 10000, tableMaxRows: 5, tableMaxCols: 6 });
    await fs.writeFile(path.join(outputDir, "synthetic-inspection.ndjson"), inspection.ndjson, { flag: "wx" });
    for (const spec of plan.sheets) {
      const preview = await workbook.render({ sheetName: spec.name, range: `A1:${columnName(Math.min(spec.headers.length, 7))}${Math.min(spec.rows.length + 1, 8)}`, scale: 1.5, format: "png" });
      await fs.writeFile(path.join(outputDir, `${spec.name}.png`), new Uint8Array(await preview.arrayBuffer()), { flag: "wx" });
    }
  }
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(path.join(outputDir, "results.xlsx"));
  if (sha(await fs.readFile(planPath)) !== sha(original)) fail("input_changed_during_export");
  const bytes = await fs.readFile(path.join(outputDir, "results.xlsx"));
  await fs.writeFile(path.join(outputDir, "generation.json"), JSON.stringify({ status: "generated_not_verified", plan_sha256: sha(original), workbook_sha256: sha(bytes), bytes: bytes.length, sheets: plan.sheets.map(s => ({ name: s.name, rows: s.rows.length })) }, null, 2), { flag: "wx" });
  process.stdout.write(JSON.stringify({ status: "generated_not_verified", sheets: plan.sheets.length }) + "\n");
} catch (error) {
  // Do not emit exception details that could contain source text or private paths.
  const safe = /^(arguments_required|invalid_\w+|unsupported_cell_text|unsupported_carriage_return|input_changed_during_export)$/.test(error.message) ? error.message : "workbook_generation_failed";
  const action = safe === "unsupported_carriage_return" ? "Preserve source text. This exporter normalizes carriage returns; exact-text export is blocked. Do not silently convert the source." : "Inspect the frozen plan, dependency availability and output collision; preserve any partial output before retrying.";
  process.stderr.write(JSON.stringify({ status: "failed", code: safe, next_action: action }) + "\n");
  process.exitCode = 1;
}

function columnName(number) {
  let name = "";
  while (number > 0) { number--; name = String.fromCharCode(65 + number % 26) + name; number = Math.floor(number / 26); }
  return name;
}
