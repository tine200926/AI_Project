// End-to-end test: loads the real extension into Chromium and points it at a
// local mock of Weverse sale pages.
//
//   node tests/e2e.mjs [--screenshots <dir>]
//
// Needs Playwright with a Chromium build that supports extensions.
import { createRequire } from 'node:module';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const shotsArg = process.argv.indexOf('--screenshots');
const SHOTS = shotsArg > 0 ? path.resolve(process.argv[shotsArg + 1]) : '';

// ---------------------------------------------------------- mock shop ----
const stock = { 'O.C.L.': true, TAMTAM: false, 'LEEHAN': true };
const seenCookies = {};

function salePage(status, opts, artist, product) {
  const options = opts.map(([n, s]) => `{"saleStockId":1,"saleOptionName":${JSON.stringify(n)},"isSoldOut":${s}}`).join(',');
  return `<!doctype html><html><head><title>${artist} | ${product}</title></head><body>
<script id="__NEXT_DATA__" type="application/json">{"props":{"sale":{"status":{"statusCode":"sale.status.${status}"},"labelArtistInfo":{"name":"${artist}"},"options":[${options}]}}}</script>
<script type="application/ld+json">{"@type":"Product","name":${JSON.stringify(product)}}</script></body></html>`;
}

const server = http.createServer((req, res) => {
  seenCookies[req.url] = req.headers.cookie || '';
  res.setHeader('content-type', 'text/html; charset=utf-8');
  if (req.url.includes('/sales/1')) {
    res.end(salePage('sale', Object.entries(stock), 'BOYNEXTDOOR', 'Face Plush Keyring'));
  } else if (req.url.includes('/sales/2')) {
    // Region-locked: only renders product data for a JP request.
    if (/wes_display_user_country=JP/.test(req.headers.cookie || '') &&
        /wes_order_user_country=JP/.test(req.headers.cookie || '')) {
      res.end(salePage('sale', [['ACRYLIC STAND ', false]], 'BOYNEXTDOOR', 'JAPAN POP-UP ACRYLIC STAND'));
    } else {
      res.end('<!doctype html><html><head><title>Weverse Shop</title></head><body>not available</body></html>');
    }
  } else if (req.url.includes('/sales/3')) {
    res.end(salePage('out.of.stock', [['S', false], ['M', false]], 'BOYNEXTDOOR', 'HOODIE'));
  } else {
    res.statusCode = 404;
    res.end('nope');
  }
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}`;

// Test build: same code, plus host access to the mock server.
const build = fs.mkdtempSync(path.join(os.tmpdir(), 'restock-ext-'));
fs.cpSync(ROOT, build, { recursive: true, filter: (p) => !p.includes(`${path.sep}tests`) && !p.includes(`${path.sep}dist`) });
const manifest = JSON.parse(fs.readFileSync(path.join(build, 'manifest.json'), 'utf8'));
manifest.host_permissions.push('http://127.0.0.1/*');
fs.writeFileSync(path.join(build, 'manifest.json'), JSON.stringify(manifest, null, 2));

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'restock-profile-'));
const context = await chromium.launchPersistentContext(profile, {
  channel: 'chromium',
  headless: true,
  args: [`--disable-extensions-except=${build}`, `--load-extension=${build}`],
});

let failures = 0;
async function step(name, fn) {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (e) {
    failures++;
    console.log(`FAIL ${name}\n     ${e.message.split('\n').join('\n     ')}`);
  }
}

try {
  let [sw] = context.serviceWorkers();
  if (!sw) sw = await context.waitForEvent('serviceworker');
  const extId = new URL(sw.url()).host;

  // Record notifications instead of relying on the OS showing them.
  await sw.evaluate(() => {
    globalThis.__notes = [];
    const orig = chrome.notifications.create.bind(chrome.notifications);
    chrome.notifications.create = (id, opts) => {
      globalThis.__notes.push({ id, ...opts });
      return orig(id, opts).catch(() => id);
    };
  });
  const notes = () => sw.evaluate(() => globalThis.__notes.map((n) => `${n.title} | ${n.message}`));

  const page = await context.newPage();
  // The install handler opens the options page by itself; use our own tab.
  await page.goto(`chrome-extension://${extId}/options.html`);
  const msg = (m) => page.evaluate((x) => chrome.runtime.sendMessage(x), m);
  const store = (keys) => page.evaluate((k) => chrome.storage.local.get(k), keys);

  const u1 = `${base}/zh-tw/shop/KRW/artists/7/sales/1`;
  const u2 = `${base}/ja/shop/JPY/artists/7/sales/2`;
  const u3 = `${base}/en/shop/USD/artists/7/sales/3`;

  await step('probe lists options and product name', async () => {
    const r = await msg({ type: 'probe', url: `${u1}?utm_source=x` });
    assert.equal(r.ok, true, r.error);
    assert.equal(r.url, u1);
    assert.equal(r.saleName, 'BOYNEXTDOOR / Face Plush Keyring');
    assert.deepEqual(r.options, [['O.C.L.', true], ['TAMTAM', false], ['LEEHAN', true]]);
  });

  await page.evaluate(([a, b, c]) => chrome.storage.local.set({
    targets: [
      { id: '1', label: 'Face Plush Keyring', url: a, watch: ['o.c.l', 'leehan'], country: '', enabled: true },
      { id: '2', label: 'JP Acrylic Stand', url: b, watch: ['*'], country: '', enabled: true },
      { id: '3', label: 'Hoodie', url: c, watch: ['*'], country: '', enabled: true },
    ],
    settings: { politeDelayMs: 0, maxRetries: 1 },
  }), [u1, u2, u3]);

  await step('first check records state without notifying', async () => {
    await msg({ type: 'checkNow' });
    const { lastRun, state } = await store(['lastRun', 'state']);
    assert.equal(lastRun.results['1'].ok, true);
    assert.deepEqual(lastRun.results['1'].options.filter((o) => o[2]).map((o) => o[0]), ['O.C.L.', 'LEEHAN']);
    assert.equal(state['1::O.C.L.'].soldOut, true);
    assert.deepEqual(await notes(), []);
  });

  await step('region cookie rule makes the JP-only page readable', async () => {
    const { lastRun } = await store('lastRun');
    assert.equal(lastRun.results['2'].ok, true, lastRun.results['2'].error);
    assert.deepEqual(lastRun.results['2'].options, [['ACRYLIC STAND', false, true]]);
    const cookie = seenCookies['/ja/shop/JPY/artists/7/sales/2'];
    assert.match(cookie, /wes_display_user_country=JP; wes_order_user_country=JP; wes_currency=JPY; wes_artistId=7/);
  });

  await step('sale-level out.of.stock overrides option flags', async () => {
    const { lastRun } = await store('lastRun');
    assert.deepEqual(lastRun.results['3'].options.map((o) => o[1]), [true, true]);
  });

  await step('restock fires one notification per product', async () => {
    stock['O.C.L.'] = false;
    stock.LEEHAN = false;
    await msg({ type: 'checkNow' });
    const n = await notes();
    assert.equal(n.length, 1, JSON.stringify(n));
    assert.match(n[0], /補貨：Face Plush Keyring \| O\.C\.L\.、LEEHAN/);
  });

  await step('badge shows available watched options', async () => {
    const text = await sw.evaluate(() => chrome.action.getBadgeText({}));
    assert.equal(text, '3'); // O.C.L., LEEHAN, ACRYLIC STAND
  });

  await step('cooldown suppresses a repeat after sold-out flicker', async () => {
    stock['O.C.L.'] = true;
    await msg({ type: 'checkNow' });
    stock['O.C.L.'] = false;
    await msg({ type: 'checkNow' });
    assert.equal((await notes()).length, 1);
  });

  await step('a failing page notifies once and sets the error badge', async () => {
    const { targets } = await store('targets');
    targets.push({ id: '9', label: 'Broken', url: `${base}/x/shop/KRW/artists/7/sales/404`, watch: ['*'], enabled: true });
    await page.evaluate((t) => chrome.storage.local.set({ targets: t }), targets);
    await msg({ type: 'checkNow' });
    await msg({ type: 'checkNow' });
    const n = (await notes()).filter((x) => x.startsWith('⚠️'));
    assert.equal(n.length, 1, JSON.stringify(n));
    assert.equal(await sw.evaluate(() => chrome.action.getBadgeText({})), '!');
    await page.evaluate((t) => chrome.storage.local.set({ targets: t.filter((x) => x.id !== '9') }), targets);
    await msg({ type: 'checkNow' });
  });

  await step('paused targets are skipped', async () => {
    const { targets } = await store('targets');
    targets[0].enabled = false;
    await page.evaluate((t) => chrome.storage.local.set({ targets: t }), targets);
    delete seenCookies['/zh-tw/shop/KRW/artists/7/sales/1'];
    await msg({ type: 'checkNow' });
    assert.equal(seenCookies['/zh-tw/shop/KRW/artists/7/sales/1'], undefined);
    targets[0].enabled = true;
    await page.evaluate((t) => chrome.storage.local.set({ targets: t }), targets);
    await msg({ type: 'checkNow' });
  });

  await step('alarm is scheduled at the configured interval', async () => {
    await page.evaluate(() => chrome.storage.local.set({ settings: { intervalMinutes: 7, politeDelayMs: 0 } }));
    await page.waitForTimeout(300);
    const a = await sw.evaluate(() => chrome.alarms.get('check'));
    assert.equal(a.periodInMinutes, 7);
  });

  await step('options page renders the list without errors', async () => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.reload();
    await page.waitForSelector('.target');
    assert.equal(await page.locator('.target').count(), 3);
    assert.deepEqual(errors, []);
  });

  await step('import accepts the PowerShell config.json shape', async () => {
    await page.fill('#io', JSON.stringify({
      targets: [{ label: 'Imported', url: 'https://shop.weverse.io/en/shop/KRW/artists/7/sales/555?x=1', watch: ['A', 'B'] },
        { label: 'dup', url: u1, watch: ['*'] }],
      line: { channelAccessToken: 'secret' },
    }));
    await page.click('#importBtn');
    await page.waitForTimeout(300);
    const { targets } = await store('targets');
    const imp = targets.find((t) => t.label === 'Imported');
    assert.ok(imp, 'imported target missing');
    assert.equal(imp.url, 'https://shop.weverse.io/en/shop/KRW/artists/7/sales/555');
    assert.deepEqual(imp.watch, ['A', 'B']);
    assert.equal(targets.length, 4); // the mock-url duplicate was skipped (and is not a shop URL)
    await page.evaluate((t) => chrome.storage.local.set({ targets: t.filter((x) => x.label !== 'Imported') }), targets);
  });

  if (SHOTS) {
    fs.mkdirSync(SHOTS, { recursive: true });
    // Store screenshots must be 1280x800 or 640x400.
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.reload();
    await page.waitForSelector('.target');
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(200);
    await page.screenshot({ path: path.join(SHOTS, 'options-1280x800.png') });
    const pop = await context.newPage();
    // Rendered at 1.6x via CSS zoom so the store image needs no upscaling.
    await pop.setViewportSize({ width: 576, height: 640 });
    await pop.goto(`chrome-extension://${extId}/popup.html`);
    await pop.waitForSelector('.item');
    await pop.evaluate(() => { document.documentElement.style.zoom = '1.6'; });
    await pop.waitForTimeout(200);
    await pop.screenshot({ path: path.join(SHOTS, 'popup.png') });
    console.log(`screenshots written to ${SHOTS}`);
  }
} finally {
  await context.close();
  server.close();
  fs.rmSync(build, { recursive: true, force: true });
  fs.rmSync(profile, { recursive: true, force: true });
}

console.log(failures ? `\n${failures} failed` : '\nall passed');
process.exit(failures ? 1 : 0);
