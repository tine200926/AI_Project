// End-to-end test: loads the real extension into Chromium and answers its
// requests to api-cms / api-book.tigerairtw.com with a mock, so it runs offline.
//
//   node tests/e2e.mjs [--screenshots <dir>]
//
// Needs Playwright with a Chromium build that supports extensions.
import { createRequire } from 'node:module';
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

// ------------------------------------------------------------ mock fares ----
const iso = (d) => d.toISOString().slice(0, 10);
const plus = (s, n) => iso(new Date(Date.parse(`${s}T00:00:00Z`) + n * 86400000));
const p2 = (n) => String(n).padStart(2, '0');
const now = new Date();
const TODAY = `${now.getFullYear()}-${p2(now.getMonth() + 1)}-${p2(now.getDate())}`;

/** Deterministic fake fare; 0 = no flight that day. */
function mockPrice(origin, dest, date) {
  const d = new Date(`${date}T00:00:00Z`);
  const seed = [...(origin + dest)].reduce((a, c) => a + c.charCodeAt(0), 0);
  if ((d.getUTCDate() + seed) % 7 === 0) return 0;
  return 1200 + ((d.getUTCDate() * 37 + seed * 11 + d.getUTCDay() * 13) % 25) * 100;
}

/** Independent brute force of what the page should find. */
function expectRoundTrips(origin, dests, start, end, minDays, maxDays, maxPrice = Infinity) {
  const trips = [];
  for (const dest of dests) {
    for (let d = start; d <= end; d = plus(d, 1)) {
      for (let n = minDays; n <= maxDays; n++) {
        const r = plus(d, n - 1);
        if (r > end) continue;
        const a = mockPrice(origin, dest, d);
        const b = mockPrice(dest, origin, r);
        if (a > 0 && b > 0 && a + b <= maxPrice) trips.push({ dest, d, r, total: a + b });
      }
    }
  }
  trips.sort((x, y) => x.total - y.total || x.d.localeCompare(y.d));
  return trips;
}

const mock = {
  requests: [],
  failRoute: '', // 'TPE-OKA' makes that direction answer HTTP 500
  failAll: false,
  failIf: null, // (query) => boolean
};

async function handleCms(route) {
  const u = new URL(route.request().url());
  const q = Object.fromEntries(u.searchParams);
  mock.requests.push(q);
  if (mock.failAll || `${q.origin}-${q.destination}` === mock.failRoute || (mock.failIf && mock.failIf(q))) {
    return route.fulfill({ status: 500, contentType: 'application/json', body: '{"message":"server error"}' });
  }
  const data = [];
  // One extra day on each side: the page must ignore dates it did not ask for.
  for (let d = plus(q.since, -1); d <= plus(q.until, 1); d = plus(d, 1)) {
    data.push({ date: d, amount: mockPrice(q.origin, q.destination, d), currency: 'TWD' });
  }
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data }) });
}

async function handleBook(route) {
  const u = new URL(route.request().url());
  if (u.pathname.endsWith('/available-destinations') && u.searchParams.get('origin') === 'TPE') {
    const menu = (code, name) => ({ station: { stationCode: code, stationName: name } });
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data: { 'zh-TW': [
        { country: { code2: 'JP' }, stationMenus: [menu('NRT', '東京'), menu('KIX', '大阪'), menu('OKA', '沖繩')] },
        { country: { code2: 'KR' }, stationMenus: [menu('ICN', '首爾')] },
        { country: { code2: 'ZZ' }, stationMenus: [menu('XX9', '測試城市')] },
      ] } }),
    });
  }
  return route.fulfill({ status: 503, body: 'down' });
}

// ---------------------------------------------------------------- browser ----
const build = fs.mkdtempSync(path.join(os.tmpdir(), 'tigerair-ext-'));
fs.cpSync(ROOT, build, { recursive: true, filter: (p) => !p.includes(`${path.sep}tests`) });
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'tigerair-profile-'));
const context = await chromium.launchPersistentContext(profile, {
  channel: 'chromium',
  headless: true,
  viewport: { width: 1180, height: 900 },
  args: [`--disable-extensions-except=${build}`, `--load-extension=${build}`],
});
await context.route('https://api-cms.tigerairtw.com/**', handleCms);
await context.route('https://api-book.tigerairtw.com/**', handleBook);

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
const shot = async (page, name) => {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, name), fullPage: true });
};

try {
  let [sw] = context.serviceWorkers();
  if (!sw) sw = await context.waitForEvent('serviceworker');
  const extId = new URL(sw.url()).host;
  const pageUrl = `chrome-extension://${extId}/search.html`;

  await step('install opens the search page', async () => {
    const deadline = Date.now() + 5000;
    while (!context.pages().some((p) => p.url() === pageUrl) && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 100));
    }
    assert.ok(context.pages().some((p) => p.url() === pageUrl), 'search.html was not opened');
  });

  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(pageUrl);

  const setDates = async (start, end) => {
    await page.fill('#start', start);
    await page.fill('#end', end);
  };
  const runSearch = async () => {
    await page.click('#go');
    await page.waitForSelector('#results:not([hidden])', { timeout: 15000 });
  };
  const chip = (code) => page.locator(`#destGroups input[value="${code}"]`);
  // Inputs are visually hidden under the chip; click the label like a user.
  const pick = async (code, on = true) => {
    if ((await chip(code).isChecked()) !== on) {
      await page.locator(`#destGroups label:has(input[value="${code}"])`).click();
    }
  };
  const tripType = (v) => page.click(`.seg label:has(input[value="${v}"])`);

  await step('route list limits destinations and adds unknown stations', async () => {
    await page.waitForFunction(() => /只列出/.test(document.getElementById('routeNote').textContent));
    assert.match(await page.textContent('#routeNote'), /台北桃園.*5 個航點/);
    assert.equal(await chip('KIX').count(), 1);
    assert.equal(await chip('HND').count(), 0, 'HND is not in the mocked route list');
    assert.equal(await chip('TPE').count(), 0, 'origin must not be a destination');
    const other = await page.locator('.dest-group', { hasText: '其他' }).textContent();
    assert.match(other, /測試城市/);
  });

  await step('header rules are installed and scoped to this extension', async () => {
    const rules = await page.evaluate(() => chrome.declarativeNetRequest.getSessionRules());
    assert.equal(rules.length, 2);
    const cms = rules.find((r) => r.condition.requestDomains.includes('api-cms.tigerairtw.com'));
    assert.deepEqual(cms.condition.initiatorDomains, [extId]);
    assert.deepEqual(cms.action.requestHeaders.map((x) => [x.header, x.value]),
      [['origin', 'https://www.tigerairtw.com'], ['referer', 'https://www.tigerairtw.com/']]);
  });

  const start = plus(TODAY, 10);
  const end = plus(TODAY, 45);

  await step('validation errors show before any request', async () => {
    mock.requests.length = 0;
    await page.click('#go');
    assert.match(await page.textContent('#errors'), /至少選一個目的地/);
    assert.equal(mock.requests.length, 0);
  });

  await step('round trip: cheapest combination across two destinations', async () => {
    await pick('KIX');
    await pick('OKA');
    assert.match(await page.textContent('#destCount'), /已選 2 個/);
    await setDates(start, end);
    await page.fill('#minDays', '3');
    await page.fill('#maxDays', '5');
    mock.requests.length = 0;
    await runSearch();

    const want = expectRoundTrips('TPE', ['KIX', 'OKA'], start, end, 3, 5);
    assert.ok(want.length > 10, 'mock should produce plenty of trips');
    assert.match(await page.textContent('#summary'), new RegExp(`找到 ${want.length.toLocaleString('en-US')} 個.*TWD ${want[0].total.toLocaleString('en-US')}`));
    const first = page.locator('#table tbody tr').first();
    assert.match(await first.textContent(), new RegExp(`TWD ${want[0].total.toLocaleString('en-US')}`));
    assert.equal(await page.locator('#table tbody tr').count(), Math.min(50, want.length));
    assert.equal(await page.locator('#best .best-card').count(), 2);

    // Requests: both directions per destination, split by month, TWD.
    const legs = new Set(mock.requests.map((r) => `${r.origin}-${r.destination}`));
    assert.deepEqual([...legs].sort(), ['KIX-TPE', 'OKA-TPE', 'TPE-KIX', 'TPE-OKA']);
    for (const r of mock.requests) {
      assert.equal(r.userCurrency, 'TWD');
      assert.equal(r.since.slice(0, 7), r.until.slice(0, 7), 'one request per month');
    }
    const out = mock.requests.filter((r) => r.origin === 'TPE').map((r) => r.since).sort();
    const back = mock.requests.filter((r) => r.destination === 'TPE').map((r) => r.until).sort();
    assert.equal(out[0], start);
    assert.equal(back.at(-1), end);

    const href = await first.locator('a.book').getAttribute('href');
    const u = new URL(href);
    assert.equal(u.host, 'booking.tigerairtw.com');
    assert.equal(u.searchParams.get('outbound'), `TPE-${want[0].dest}`);
    assert.equal(u.searchParams.get('departureDate'), want[0].d);
    assert.equal(u.searchParams.get('returnDate'), want[0].r);
    await shot(page, 'results-roundtrip.png');
  });

  await step('show more and sort by date', async () => {
    const total = expectRoundTrips('TPE', ['KIX', 'OKA'], start, end, 3, 5).length;
    if (total > 50) {
      await page.click('#more');
      assert.equal(await page.locator('#table tbody tr').count(), Math.min(100, total));
    }
    await page.selectOption('#sortBy', 'date');
    const dates = await page.$$eval('#table tbody tr td:nth-child(3)', (tds) => tds.map((t) => t.textContent));
    assert.deepEqual(dates, [...dates].sort());
    await page.selectOption('#sortBy', 'price');
  });

  await step('a second identical search is served from cache', async () => {
    mock.requests.length = 0;
    await runSearch();
    assert.equal(mock.requests.length, 0);
  });

  await step('price ceiling filters results', async () => {
    const want = expectRoundTrips('TPE', ['KIX', 'OKA'], start, end, 3, 5, 3500);
    await page.fill('#maxPrice', '3500');
    await runSearch();
    if (want.length) {
      assert.match(await page.textContent('#summary'), new RegExp(`找到 ${want.length} 個`));
      const totals = await page.$$eval('#table td.total', (tds) => tds.map((t) => Number(t.textContent.replace(/\D/g, ''))));
      assert.ok(totals.every((t) => t <= 3500));
    }
    await page.fill('#maxPrice', '100');
    await runSearch();
    assert.equal(await page.textContent('#summary'), '沒有符合條件的機票');
    await page.fill('#maxPrice', '');
  });

  await step('form is remembered after reopening', async () => {
    await page.reload();
    await page.waitForFunction(() => /只列出/.test(document.getElementById('routeNote').textContent));
    assert.equal(await chip('KIX').isChecked(), true);
    assert.equal(await chip('OKA').isChecked(), true);
    assert.equal(await page.inputValue('#start'), start);
    assert.equal(await page.inputValue('#end'), end);
    assert.equal(await page.inputValue('#maxPrice'), '', 'edits made after the last search are kept too');
  });

  await step('one failing route warns but others still show', async () => {
    mock.failRoute = 'OKA-TPE';
    await runSearch(); // fresh page, so nothing is cached
    mock.failRoute = '';
    assert.match(await page.textContent('#warnings'), /沖繩：部分日期查詢失敗.*HTTP 500/);
    assert.match(await page.textContent('#summary'), /找到/);
    const dests = await page.$$eval('#table tbody tr td:nth-child(2)', (tds) => tds.map((t) => t.textContent));
    assert.ok(dests.every((d) => d.includes('KIX')), 'only KIX trips can be complete');
  });

  await step('a failed month on every destination still shows the rest', async () => {
    // Fresh dates (no cache); the first month of every return leg fails.
    const s2 = plus(TODAY, 80);
    const e2 = plus(TODAY, 140);
    await setDates(s2, e2);
    mock.failIf = (q) => q.destination === 'TPE' && q.since === plus(s2, 2);
    await runSearch();
    mock.failIf = null;
    const warn = await page.textContent('#warnings');
    assert.match(warn, /大阪關西：部分日期查詢失敗/);
    assert.match(warn, /沖繩：部分日期查詢失敗/);
    assert.doesNotMatch(warn, /查詢失敗：/);
    assert.match(await page.textContent('#summary'), /找到/);
    await setDates(start, end);
  });

  await step('one-way search', async () => {
    await tripType('oneWay');
    assert.equal(await page.isHidden('#daysField'), true);
    await pick('OKA', false);
    await page.fill('#extraCodes', 'xx9');
    await runSearch();
    const heads = await page.$$eval('#table thead th', (ths) => ths.map((t) => t.textContent));
    assert.deepEqual(heads, ['#', '目的地', '出發日', '票價', '']);
    let cheapest = Infinity;
    for (const dest of ['KIX', 'XX9']) {
      for (let d = start; d <= end; d = plus(d, 1)) {
        const p = mockPrice('TPE', dest, d);
        if (p > 0) cheapest = Math.min(cheapest, p);
      }
    }
    assert.match(await page.textContent('#summary'), new RegExp(`TWD ${cheapest.toLocaleString('en-US')}`));
    const href = await page.locator('#table a.book').first().getAttribute('href');
    assert.equal(new URL(href).searchParams.get('type'), 'oneWay');
    await shot(page, 'results-oneway.png');
    await page.fill('#extraCodes', '');
    await tripType('roundTrip');
  });

  await step('route list down: all stations are offered', async () => {
    await page.selectOption('#origin', 'KHH');
    await page.waitForFunction(() => /讀不到/.test(document.getElementById('routeNote').textContent));
    assert.equal(await chip('HND').count(), 1);
    assert.equal(await chip('KHH').count(), 0);
    assert.equal(await chip('TPE').count(), 1);
  });

  await step('every request failing shows one clear error', async () => {
    mock.failAll = true;
    await pick('HND');
    await setDates(plus(TODAY, 50), plus(TODAY, 70)); // uncached range
    await runSearch();
    mock.failAll = false;
    assert.match(await page.textContent('#warnings'), /查詢失敗：.*HTTP 500/);
    assert.equal(await page.textContent('#summary'), '查詢失敗');
    assert.equal(await page.isHidden('#tableWrap'), true);
    assert.equal(await page.isEnabled('#go'), true);
  });

  await step('no page errors', async () => {
    assert.deepEqual(errors, []);
  });

  if (SHOTS) {
    await page.selectOption('#origin', 'TPE');
    await page.waitForFunction(() => /只列出/.test(document.getElementById('routeNote').textContent));
    await page.setViewportSize({ width: 390, height: 844 });
    await shot(page, 'mobile.png');
  }
} finally {
  await context.close();
  fs.rmSync(build, { recursive: true, force: true });
  fs.rmSync(profile, { recursive: true, force: true });
}

if (failures) {
  console.log(`\n${failures} step(s) failed`);
  process.exit(1);
}
console.log('\nall e2e steps passed');
