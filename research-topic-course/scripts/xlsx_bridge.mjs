// JSON tables -> XLSX via the host's supported Artifact Tool; no network access.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const modules = process.env.RESEARCH_COURSE_NODE_MODULES || path.join(os.homedir(), '.cache', 'codex-runtimes', 'codex-primary-runtime', 'dependencies', 'node', 'node_modules');
const require = createRequire(path.join(modules, '__course_resolve__.cjs'));
const { Workbook, SpreadsheetFile } = await import(pathToFileURL(require.resolve('@oai/artifact-tool')).href);
const [specFile, outFile, previewFile] = process.argv.slice(2);
if (!specFile || !outFile) throw new Error('usage: xlsx_bridge.mjs spec.json output.xlsx [preview.png]');
try { await fs.access(outFile); throw new Error(`Refusing overwrite: ${outFile}`); }
catch (e) { if (e.code !== 'ENOENT') throw e; }
const spec = JSON.parse(await fs.readFile(specFile, 'utf8'));
const wb = Workbook.create();
for (const table of spec.sheets) {
  const sh = wb.worksheets.add(table.name);
  const values = [table.headers, ...table.rows];
  if (values.length > 1048576) throw new Error('Excel row limit exceeded');
  const safe = values.map(row => row.map(v => {
    if (typeof v === 'string' && v.length > 32767) throw new Error('Excel cell length exceeded; preserve full text externally before retry');
    return typeof v === 'string' && v.startsWith('=') ? "'" + v : v;
  }));
  sh.getRangeByIndexes(0, 0, values.length, table.headers.length).values = safe;
  const used = sh.getRangeByIndexes(0, 0, values.length, table.headers.length);
  used.format.font.name = 'Microsoft YaHei';
  used.format.font.size = 10;
  used.format.columnWidth = 23;
  used.format.wrapText = true;
  used.format.verticalAlignment = 'top';
  sh.getRangeByIndexes(0, 0, 1, table.headers.length).format.fill = '#DDEBF7';
  sh.getRangeByIndexes(0, 0, 1, table.headers.length).format.font.bold = true;
  sh.freezePanes.freezeRows(1);
  used.format.autofitRows();
}
wb.recalculate();
await fs.mkdir(path.dirname(path.resolve(outFile)), {recursive:true});
await (await SpreadsheetFile.exportXlsx(wb)).save(outFile);
if (previewFile) {
  const blob = await wb.render({sheetName:spec.sheets[0].name,range:'A1:E8',scale:1,format:'png'});
  await fs.writeFile(previewFile, new Uint8Array(await blob.arrayBuffer()));
}
process.stdout.write(JSON.stringify({output:outFile, sheets:spec.sheets.length}));
