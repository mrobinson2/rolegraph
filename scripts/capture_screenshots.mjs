/** Browser acceptance checks and screenshots against an isolated demo database.
 * Optional development tooling only; see docs/TESTING_AND_SCREENSHOTS.md.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, mkdir, writeFile, rm, realpath } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(path.resolve(process.env.ROLEGRAPH_BROWSER_MODULES || path.join(root, '.tools/browser')), 'package.json'));
const { chromium } = require('playwright');
const output = path.resolve(process.argv[2] || path.join(root, 'artifacts/screenshots'));
const temporary = await realpath(await mkdtemp(path.join(tmpdir(), 'rolegraph-browser-')));
const python = process.env.ROLEGRAPH_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const server = spawn(python, ['-m', 'uvicorn', 'rolegraph.web.app:app', '--host', '127.0.0.1', '--port', '0'], {
  cwd: root,
  env: { ...process.env, ROLEGRAPH_DATABASE_URL: `sqlite:///${path.join(temporary, 'demo.db').replaceAll('\\', '/')}`,
    ROLEGRAPH_DEMO_DATASET: path.join(root, 'data/demo/contoso.json'),
    ROLEGRAPH_PRIVILEGED_ROLES_FILE: path.join(root, 'config/privileged_roles.yaml') },
  stdio: ['ignore', 'pipe', 'pipe'],
});
let browser;
let serverLog = '';
const failures = [];
const captures = [];
try {
  const base = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Server startup timed out: ${serverLog}`)), 15000);
    const read = chunk => {
      serverLog += chunk.toString();
      const match = serverLog.match(/Uvicorn running on (http:\/\/127\.0\.0\.1:\d+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    };
    server.stdout.on('data', read);
    server.stderr.on('data', read);
    server.once('error', error => { clearTimeout(timer); reject(error); });
    server.once('exit', code => { clearTimeout(timer); reject(new Error(`Server exited ${code}: ${serverLog}`)); });
  });
  browser = await chromium.launch({ channel: process.env.ROLEGRAPH_BROWSER_CHANNEL || 'chrome', headless: true });
  const context = await browser.newContext();
  await context.route('**/*', async route => {
    if (new URL(route.request().url()).origin !== base) {
      failures.push(`Unexpected outbound browser request: ${route.request().url()}`);
      await route.abort();
    } else await route.continue();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.on('pageerror', error => failures.push(error.message));
  page.on('console', message => { if (message.type() === 'error') failures.push(message.text()); });
  page.on('response', response => { if (response.status() >= 500) failures.push(`HTTP ${response.status()}: ${response.url()}`); });
  await mkdir(output, { recursive: true });
  // Invalidate any earlier success before replacing images in a rerun.
  await writeFile(path.join(output, 'manifest.json'), JSON.stringify({ passed: false, startedAt: new Date().toISOString() }) + '\n');
  async function goto(route) {
    const response = await page.goto(base + route);
    assert.equal(response.status(), 200, route);
  }
  async function capture(name, size) {
    await page.setViewportSize(size);
    await page.evaluate(() => document.fonts.ready);
    const layout = await page.evaluate(() => ({
      viewport: innerWidth, document: document.documentElement.scrollWidth,
      clippedNav: [...document.querySelectorAll('nav.main a')].some(a => {
        const r = a.getBoundingClientRect(), parent = a.closest('header').getBoundingClientRect();
        return r.top < parent.top || r.bottom > parent.bottom || r.right > innerWidth;
      }),
    }));
    assert.ok(layout.document <= layout.viewport + 1, `${name}: document overflows ${JSON.stringify(layout)}`);
    assert.equal(layout.clippedNav, false, `${name}: navigation is clipped`);
    const filename = `${name}-${size.width}.png`;
    await page.screenshot({ path: path.join(output, filename), fullPage: true });
    captures.push({ file: filename, route: new URL(page.url()).pathname, viewport: size, layout });
  }
  const desktop = { width: 1440, height: 1000 }, mobile = { width: 390, height: 844 };
  async function both(name) { await capture(name, desktop); await capture(name, mobile); }

  await goto('/');
  await page.keyboard.press('Tab');
  assert.equal(await page.locator(':focus').textContent(), 'Skip to content');
  await page.keyboard.press('Enter');
  assert.equal(await page.locator(':focus').getAttribute('id'), 'main-content');
  await both('01-empty');
  await page.getByRole('button', { name: 'Load the Contoso demo dataset' }).click();
  await page.getByText('Imported 73 records', { exact: true }).waitFor();
  await both('02-imported');
  await goto('/');
  await both('03-overview');
  await goto('/identities');
  await page.getByRole('searchbox', { name: 'Search identities' }).fill('Jane');
  await page.getByRole('status').filter({ hasText: '1 of 16 identities' }).waitFor();
  await both('04-search');
  await page.getByRole('link', { name: 'Jane Smith', exact: true }).click();
  const fill = await page.locator('svg.relationship .node a rect').first().evaluate(el => getComputedStyle(el).fill);
  assert.equal(fill, 'rgba(0, 0, 0, 0)', 'Diagram link overlays must not obscure labels');
  await both('05-identity');
  await capture('05-identity', { width: 768, height: 1024 });
  await page.getByRole('link', { name: 'Access path', exact: true }).first().click();
  await page.getByRole('heading', { name: 'Access path', exact: true }).waitFor();
  await both('06-access-path');
  await goto('/findings?severity=high');
  await both('07-findings');
  await goto('/roles');
  await page.getByRole('searchbox', { name: 'Search roles' }).fill('Legacy-Auditor');
  await page.getByRole('status').filter({ hasText: '1 of 9 roles' }).waitFor();
  await both('08-roles');
  await page.getByRole('link', { name: 'Contoso-Legacy-Auditor', exact: true }).click();
  await both('09-role-detail');
  await goto('/scopes');
  await both('10-scopes');
  await goto('/privileged');
  await both('11-privileged');
  await goto('/identities');
  await page.getByRole('searchbox', { name: 'Search identities' }).fill('no-such-identity-123');
  await page.getByRole('status').filter({ hasText: '0 of 16 identities' }).waitFor();
  await both('12-no-results');
  await goto('/import');
  await page.getByLabel('Dataset JSON file').setInputFiles({ name: 'invalid.json', mimeType: 'application/json', buffer: Buffer.from('{invalid') });
  await page.getByRole('button', { name: 'Import file', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: 'not valid JSON' }).waitFor();
  await both('13-import-error');
  await goto('/');
  assert.ok(await page.getByRole('heading').filter({ hasText: 'Contoso' }).count(), 'Invalid upload must preserve the active demo');
  assert.deepEqual(failures, [], 'Browser must have no errors or outbound requests');
  const manifest = { passed: true, capturedAt: new Date().toISOString(), browser: browser.version(), dataset: 'data/demo/contoso.json', captures, failures };
  await writeFile(path.join(output, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
  const figures = captures.map(c => `<figure><a href="${c.file}"><img loading="lazy" src="${c.file}" alt="${c.file}"></a><figcaption>${c.file}</figcaption></figure>`).join('\n');
  await writeFile(path.join(output, 'index.html'), `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>RoleGraph screen review</title><style>body{font:16px system-ui;background:#f6f7f9;color:#1c2530;margin:32px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:24px}figure{margin:0}img{width:100%;height:320px;object-fit:contain;object-position:top;background:white;border:1px solid #dfe3e8}figcaption{padding:8px}</style><h1>RoleGraph screen review</h1><p>Synthetic Contoso data. Click any screen for the full image. Browser checks passed; see manifest.json.</p><main>${figures}</main></html>`);
  console.log(`PASS: ${captures.length} screenshots; search, access path, import validation, keyboard navigation, layout and browser network checks. Gallery: ${path.join(output, 'index.html')}`);
} finally {
  await browser?.close();
  if (server.exitCode === null) {
    const exited = once(server, 'exit');
    server.kill('SIGTERM');
    const force = setTimeout(() => server.kill('SIGKILL'), 5000);
    await exited;
    clearTimeout(force);
  }
  await rm(temporary, { recursive: true, force: true });
}
