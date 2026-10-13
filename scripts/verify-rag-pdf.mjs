// Read-only PDF extraction using the real browser helper, followed by a
// rollback-only PostgreSQL/API check. Never prints source text or credentials.
// node scripts/verify-rag-pdf.mjs <PDF path> <physical pages, e.g. 20,21,22>
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { resolve, basename } from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = fileURLToPath(new URL('../', import.meta.url));
let browser, server;
try {
  const [input, selection] = process.argv.slice(2);
  if (!input || !/^\d+(,\d+){0,9}$/.test(selection || ''))
    throw Error('Usage: node scripts/verify-rag-pdf.mjs <PDF> <1-10 physical pages, comma-separated>');
  const pages = [...new Set(selection.split(',').map(Number))];
  if (pages.some(p => p < 1)) throw Error('Pages must be positive integers.');
  const pdfPath = resolve(input);
  if ((await stat(pdfPath)).size > 25 * 1024 * 1024) throw Error('The existing upload limit is 25MB.');
  try { process.loadEnvFile(resolve(root, '.env')); } catch (e) { if (e.code !== 'ENOENT') throw e; }
  const dsn = process.env.HAEDAP_TEST_DATABASE_URL || process.env.DATABASE_URL;
  if (!dsn) throw Error('Configure DATABASE_URL or HAEDAP_TEST_DATABASE_URL. Verification uses a rolled-back temporary schema.');
  const pdfBytes = await readFile(pdfPath);
  server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost');
      if (url.pathname === '/') { res.end('<html></html>'); return; }
      if (url.pathname === '/source.pdf') { res.setHeader('Content-Type','application/pdf'); res.end(pdfBytes); return; }
      let file;
      if (url.pathname === '/pdf-utils.js') file = resolve(root, 'frontend/src/lib/pdf.js');
      else if (/^\/vendor\/pdfjs\/[a-zA-Z0-9_./-]+$/.test(url.pathname)) {
        const base = resolve(root, 'frontend/public/vendor/pdfjs');
        file = resolve(root, 'frontend/public', '.' + url.pathname);
        if (!file.startsWith(base + '/') && !file.startsWith(base + '\\')) { res.writeHead(404).end(); return; }
      } else { res.writeHead(404).end(); return; }
      res.setHeader('Content-Type', /\.(m?js)$/.test(file) ? 'text/javascript' : 'application/octet-stream');
      res.end(await readFile(file));
    } catch { res.writeHead(404).end(); }
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const launch = {headless: true};
  if (process.env.CHROME_PATH) launch.executablePath = process.env.CHROME_PATH;
  else if (process.platform === 'win32') launch.channel = 'msedge';
  browser = await chromium.launch(launch);
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const sample = await page.evaluate(async ({pages, name}) => {
    const {extractPdf, textToSections, sectionsToText} = await import('/pdf-utils.js');
    const file = new File([await (await fetch('/source.pdf')).arrayBuffer()], name, {type:'application/pdf'});
    const result = await extractPdf(file);
    const selected = result.sections.filter(s => pages.includes(s.page));
    if (selected.length !== pages.length) throw Error('A selected page is empty or out of range.');
    return {sections:textToSections(sectionsToText(selected), 'Local PDF verification'), file:result.file,
            extractedPages:result.sections.length, emptyPages:result.emptyPages.length};
  }, {pages, name:basename(pdfPath)});
  await browser.close(); browser = null;
  console.log(`PDF.js extraction OK; text pages=${sample.extractedPages}, empty pages=${sample.emptyPages}, verifying=${pages.join(',')}`);
  const child = spawn(process.env.HAEDAP_UV || 'uv', ['run','--locked','--project','backend','python','scripts/verify-rag-pdf.py'], {
    cwd: root, windowsHide: true, stdio:['pipe','inherit','inherit'],
    env:{...process.env, HAEDAP_TEST_DATABASE_URL:dsn, OPENAI_API_KEY:'', OPENAI_MODEL:'', PYTHONUTF8:'1'},
  });
  child.stdin.on('error', () => {});
  const done = new Promise((resolve, reject) => { child.on('error', reject); child.on('close', resolve); });
  child.stdin.end(JSON.stringify(sample));
  const code = await done;
  if (code !== 0) throw Error('PostgreSQL verification failed; see sanitized diagnostics above.');
} catch (error) {
  // Browser/IO diagnostics cannot contain DB passwords; do not print child env.
  console.error(error.message);
  process.exitCode = 1;
} finally {
  await browser?.close();
  if (server) await new Promise(r => server.close(r));
}
