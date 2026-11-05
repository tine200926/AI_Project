// End-to-end test: loads the real extension into Chromium and answers its
// requests to api-cms / api-book.tigerairtw.com with a mock, so it runs offline.
// The booking site (booking.tigerairtw.com) is a local HTTPS server that the
// browser reaches through --host-resolver-rules: Playwright's request routing
// can't see the first load of a tab the extension opens itself.
//
//   node tests/e2e.mjs [--screenshots <dir>]
//
// Needs Playwright with a Chromium build that supports extensions.
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import https from 'node:https';
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
const shortDateOf = (s) => `${s.slice(5, 7)}/${s.slice(8, 10)}`;
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

// ------------------------------------------------- mock booking site ----
// Three flights a day plus a sold-out one; each costs the calendar price plus
// "tax" plus a surcharge that rotates by date, so the cheapest time changes.
const FLIGHT_TIMES = ['07:05', '13:30', '19:45'];
const OFFSETS = [0, 400, 800];
function flightPrice(o, d, date, time) {
  const base = mockPrice(o, d, date);
  const i = FLIGHT_TIMES.indexOf(time);
  if (!base || i < 0) return 0;
  return base + 300 + OFFSETS[(i + Number(date.slice(8))) % 3];
}
function mockJourney(o, d, date) {
  const seg = (time) => ({
    origin: o, destination: d, departureTime: `${date} ${time}:00`, arrivalTime: `${date} 23:00:00`,
    carrierCode: 'IT', flightNumber: ` ${100 + FLIGHT_TIMES.indexOf(time)}`,
  });
  const fare = (total, sellable = true) => ({
    sellable, productClass: 'tigerlight', availableCount: 4,
    paxFares: [{ paxType: 'ADT', ticketPrice: { totalAmount: total, discountedTotalAmount: 0, userCurrency: 'TWD' } }],
  });
  const legs = mockPrice(o, d, date) ? [
    ...FLIGHT_TIMES.map((t) => ({ availabilitySegments: [seg(t)], fares: [fare(flightPrice(o, d, date, t) + 900), fare(flightPrice(o, d, date, t))] })),
    { availabilitySegments: [seg('10:00')], fares: [fare(100, false)] }, // sold out
  ] : [];
  return { legs: [{ availabilityLegs: legs }] };
}
const bookingSearches = [];

const BOOKING_PAGE = `<!doctype html><html><head><meta charset="utf-8"><title>mock booking</title></head><body>
<p id="state">loading</p>
<script>
(async () => {
  const q = Object.fromEntries(new URLSearchParams(location.search));
  if (!q.outbound) { document.getElementById('state').textContent = 'done'; return; }
  await new Promise((r) => setTimeout(r, 150));
  history.replaceState(null, '', '/zh-TW/flight-result'); // like the real SPA
  const api = 'https://api-book.tigerairtw.com/graphql';
  await fetch(api, { method: 'POST', body: JSON.stringify({ operationName: 'appConfig' }) }).then((r) => r.text());
  const body = JSON.stringify({ operationName: 'appFlightSearchResult', variables: q });
  if (q.type === 'oneWay') {
    await new Promise((resolve) => {
      const x = new XMLHttpRequest();
      x.open('POST', api);
      x.onload = resolve;
      x.send(body);
    });
  } else {
    await fetch(api, { method: 'POST', body }).then((r) => r.json());
  }
  document.getElementById('state').textContent = 'done';
})();
</script></body></html>`;

function graphqlAnswer(postData) {
  const req = JSON.parse(postData || '{}');
  if (req.operationName !== 'appFlightSearchResult') return '{"data":{"appConfig":{}}}';
  const v = req.variables;
  bookingSearches.push(v);
  const [o, d] = v.outbound.split('-');
  const journeys = [mockJourney(o, d, v.departureDate)];
  if (v.type === 'roundTrip') journeys.push(mockJourney(d, o, v.returnDate));
  return JSON.stringify({ data: { appFlightSearchResult: { journeys } } });
}
const CORS = { 'access-control-allow-origin': 'https://booking.tigerairtw.com' };

async function handleGraphql(route) {
  return route.fulfill({ status: 200, headers: CORS, contentType: 'application/json', body: graphqlAnswer(route.request().postData()) });
}

// Local HTTPS stand-in for booking.tigerairtw.com (and its GraphQL calls).
const certDir = fs.mkdtempSync(path.join(os.tmpdir(), 'tigerair-cert-'));
execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=booking.tigerairtw.com',
  '-keyout', path.join(certDir, 'key.pem'), '-out', path.join(certDir, 'cert.pem')], { stdio: 'ignore' });
const site = https.createServer({
  key: fs.readFileSync(path.join(certDir, 'key.pem')),
  cert: fs.readFileSync(path.join(certDir, 'cert.pem')),
}, (req, res) => {
  const host = (req.headers.host || '').split(':')[0];
  if (host === 'api-book.tigerairtw.com' && req.url.startsWith('/graphql')) {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      res.writeHead(200, { ...CORS, 'content-type': 'application/json' });
      res.end(graphqlAnswer(body));
    });
    return;
  }
  if (host === 'booking.tigerairtw.com') {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    res.end(BOOKING_PAGE);
    return;
  }
  res.writeHead(404);
  res.end();
});
await new Promise((r) => site.listen(0, '127.0.0.1', r));
const sitePort = site.address().port;

async function handleBook(route) {
  const u = new URL(route.request().url());
  if (u.pathname === '/graphql') return handleGraphql(route);
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
  args: [
    `--disable-extensions-except=${build}`,
    `--load-extension=${build}`,
    `--host-resolver-rules=MAP booking.tigerairtw.com 127.0.0.1:${sitePort}, MAP api-book.tigerairtw.com 127.0.0.1:${sitePort}`,
    '--ignore-certificate-errors',
    '--no-proxy-server',
  ],
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

  const bookingTabs = () => context.pages().filter((p) => p.url().includes('booking.tigerairtw.com'));
  const waitVerified = async () => {
    try {
      await page.waitForFunction(() => {
        const v = document.getElementById('verified');
        return !v.hidden && document.getElementById('vStop').hidden && document.getElementById('vStatus').textContent;
      }, null, { timeout: 120000 });
    } catch (e) {
      const st = await page.evaluate(() => ['verified', 'vSummary', 'vStatus', 'vWarnings'].map((id) => {
        const el = document.getElementById(id);
        return `${id}: hidden=${el.hidden} ${el.textContent.trim().slice(0, 200)}`;
      }).join('\n'));
      throw new Error(`${e.message.split('\n')[0]}\n${st}\nbooking searches: ${bookingSearches.length}, open booking tabs: ${bookingTabs().map((p) => p.url()).join(' ')}`);
    }
  };

  await step('departure-time filter checks real flights on the booking page', async () => {
    const s3 = plus(TODAY, 10);
    const e3 = plus(TODAY, 19);
    await setDates(s3, e3);
    await page.fill('#minDays', '4');
    await page.fill('#maxDays', '4');
    await page.fill('#outTimeFrom', '06:00');
    await page.fill('#outTimeTo', '12:00');
    await page.fill('#backTimeFrom', '15:00');
    await page.fill('#backTimeTo', '23:59');
    bookingSearches.length = 0;
    await runSearch();
    await waitVerified();

    let best = null;
    for (let d = s3; plus(d, 3) <= e3; d = plus(d, 1)) {
      const out = flightPrice('TPE', 'KIX', d, '07:05');
      const back = flightPrice('KIX', 'TPE', plus(d, 3), '19:45');
      if (out && back && (!best || out + back < best.total)) best = { d, total: out + back };
    }
    assert.ok(best, 'mock should have a matching trip');
    const status = await page.textContent('#vStatus');
    assert.match(status, /已確認|確認完了/, status);
    assert.match(await page.textContent('#vSummary'), new RegExp(`去程 06:00～12:00、回程 15:00～23:59 出發：最便宜 TWD ${best.total.toLocaleString('en-US')}（含稅）`));
    const row = await page.locator('#vTable tbody tr').first().textContent();
    assert.match(row, /IT100 07:05/);
    assert.match(row, /IT102 19:45/);
    assert.match(row, new RegExp(shortDateOf(best.d)));
    assert.ok(bookingSearches.length >= 1 && bookingSearches.length <= 10, `searches: ${bookingSearches.length}`);
    assert.ok(bookingSearches.some((v) => v.type === 'roundTrip'));
    assert.equal(bookingTabs().length, 0, 'check tabs must be closed');
    assert.equal(await page.isVisible('#resultsNote'), true);
    await shot(page, 'results-time-filter.png');
  });

  await step('one-way departure-time filter (page uses XHR)', async () => {
    await tripType('oneWay');
    await page.fill('#outTimeFrom', '18:00');
    await page.fill('#outTimeTo', '');
    bookingSearches.length = 0;
    await runSearch();
    await waitVerified();
    let best = Infinity;
    for (let d = plus(TODAY, 10); d <= plus(TODAY, 19); d = plus(d, 1)) {
      const p = flightPrice('TPE', 'KIX', d, '19:45');
      if (p) best = Math.min(best, p);
    }
    assert.match(await page.textContent('#vSummary'), new RegExp(`去程 18:00～23:59 出發：最便宜 TWD ${best.toLocaleString('en-US')}`));
    assert.ok(bookingSearches.every((v) => v.type === 'oneWay'));
    assert.equal(bookingTabs().length, 0);
    await tripType('roundTrip');
    await page.click('#tmClear');
    await setDates(start, end);
    await page.fill('#minDays', '3');
    await page.fill('#maxDays', '5');
  });

  await step('booking pages the user opens are not hooked', async () => {
    const plain = await context.newPage();
    await plain.goto(`https://booking.tigerairtw.com/?type=oneWay&outbound=TPE-KIX&departureDate=${start}`);
    await plain.waitForFunction(() => document.getElementById('state').textContent === 'done');
    assert.equal(await plain.evaluate(() => window.fetch.toString().includes('[native code]')), true);
    const marked = await context.newPage();
    await marked.goto(`https://booking.tigerairtw.com/?type=oneWay&outbound=TPE-KIX&departureDate=${start}#tigerair-finder`);
    await marked.waitForFunction(() => document.getElementById('state').textContent === 'done');
    assert.equal(await marked.evaluate(() => window.fetch.toString().includes('[native code]')), false);
    await plain.close();
    await marked.close();
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
  site.close();
  fs.rmSync(certDir, { recursive: true, force: true });
  fs.rmSync(build, { recursive: true, force: true });
  fs.rmSync(profile, { recursive: true, force: true });
}

if (failures) {
  console.log(`\n${failures} step(s) failed`);
  process.exit(1);
}
console.log('\nall e2e steps passed');
