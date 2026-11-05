// End-to-end test: loads the real extension into Chromium and serves a mock
// of the myship coupon page through Playwright request interception.
//
//   node tests/e2e.mjs
//
// The mock server's clock runs SKEW ms ahead of the computer, so the test
// also checks that the extension fires on the site's time, not local time.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SKEW = 1500;
const serverNow = () => Date.now() + SKEW;
const { createMyship } = await import('./myship-mock.mjs');
const myship = createMyship(serverNow);
let mode = 'simple'; // 'simple' mock page, or 'myship' (realistic AJAX page)

// ---------------------------------------------------------- mock site ----
const site = {
  openAt: Infinity,          // server time when claiming opens
  claimed: {},               // id -> server time of successful claim
  early: 0,                  // claim attempts before openAt
  loads: [],                 // server times of coupon-page loads
  loginRequired: false,
};
const COUPONS = [
  { id: 'A', name: '免運券 滿 0 元可用' },
  { id: 'B', name: '50元折價券 滿 299 可用' },
  { id: 'C', name: '9折券', soldOut: true },
];

function page() {
  const open = serverNow() >= site.openAt;
  const cards = COUPONS.map((c) => {
    let btn;
    if (c.soldOut) btn = '<button class="btn" disabled>已領完</button>';
    else if (site.claimed[c.id]) btn = '<button class="btn">已領取</button>';
    else if (!open) btn = '<button class="btn btn-disabled">即將開放</button>';
    else btn = `<a href="javascript:void(0)" class="btn" onclick="claim('${c.id}', this)"><span>領取</span></a>`;
    return `<div class="coupon"><div class="title">${c.name}</div><div class="desc">使用期限 2026/11/30</div>${btn}</div>`;
  }).join('');
  return `<!doctype html><html><head><meta charset="utf-8"><title>領取優惠券</title></head><body>
<h1>領取優惠券</h1><div id="list"></div>
<script>
  // Render late, like a JS-driven page.
  setTimeout(() => { document.getElementById('list').innerHTML = ${JSON.stringify(cards)}; }, 300);
  async function claim(id, el) {
    if (!confirm('確定要領取嗎？')) return;
    const r = await fetch('/mem/Claim?id=' + id, { method: 'POST' });
    const j = await r.json();
    if (j.ok) { el.outerHTML = '<button class="btn">已領取</button>'; alert('領取成功'); }
    else alert(j.msg);
  }
</script></body></html>`;
}

async function handle(route) {
  const req = route.request();
  const url = new URL(req.url());
  const headers = { date: new Date(serverNow()).toUTCString(), 'cache-control': 'no-store' };
  if (mode === 'myship') {
    const done = await myship.handle(route, headers);
    if (done !== null) return done;
  }
  if (url.pathname === '/mem/GetCoupon') {
    if (req.method() === 'HEAD') return route.fulfill({ status: 200, headers, body: '' });
    if (site.loginRequired) {
      return route.fulfill({ status: 302, headers: { ...headers, location: 'https://myship.7-11.com.tw/Login?returnUrl=/mem/GetCoupon' } });
    }
    site.loads.push(serverNow());
    return route.fulfill({ status: 200, headers: { ...headers, 'content-type': 'text/html; charset=utf-8' }, body: page() });
  }
  if (url.pathname === '/mem/Claim') {
    const id = url.searchParams.get('id');
    let body;
    if (serverNow() < site.openAt) { site.early++; body = { ok: false, msg: '活動尚未開始' }; }
    else if (site.claimed[id]) body = { ok: false, msg: '您已領取過' };
    else { site.claimed[id] = serverNow(); body = { ok: true }; }
    return route.fulfill({ status: 200, headers: { ...headers, 'content-type': 'application/json' }, body: JSON.stringify(body) });
  }
  if (url.pathname === '/Login') {
    return route.fulfill({ status: 200, headers: { ...headers, 'content-type': 'text/html; charset=utf-8' }, body: '<!doctype html><title>登入</title><h1>請登入</h1>' });
  }
  return route.fulfill({ status: 404, headers, body: 'nope' });
}

// ------------------------------------------------------------ harness ----
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'coupon-profile-'));
const context = await chromium.launchPersistentContext(profile, {
  channel: 'chromium',
  headless: true,
  args: [`--disable-extensions-except=${ROOT}`, `--load-extension=${ROOT}`],
});
await context.route('https://myship.7-11.com.tw/**', handle);

let sw = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
const extId = new URL(sw.url()).host;
const opts = await context.newPage();
await opts.goto(`chrome-extension://${extId}/options.html`);

let failures = 0;
async function step(name, fn) {
  try { await fn(); console.log(`ok   ${name}`); } catch (e) {
    failures++;
    console.log(`FAIL ${name}\n     ${String(e.stack || e).split('\n').slice(0, 4).join('\n     ')}`);
  }
}
const store = (keys) => opts.evaluate((k) => chrome.storage.local.get(k), keys);
const setStore = (obj) => opts.evaluate((o) => chrome.storage.local.set(o), obj);
async function waitFor(fn, ms, what) {
  const until = Date.now() + ms;
  while (Date.now() < until) { if (await fn()) return; await new Promise((r) => setTimeout(r, 200)); }
  throw new Error(`timed out waiting for ${what}`);
}
const lastLog = async () => ((await store('log')).log || [])[0]?.msg || '';
const pad = (n) => String(n).padStart(2, '0');
function resetSite(openAt) {
  site.openAt = openAt; site.claimed = {}; site.early = 0; site.loads = []; site.loginRequired = false;
}

await step('options page renders defaults', async () => {
  assert.equal(await opts.inputValue('#leadSec'), '60');
  assert.equal(await opts.isChecked('#timeSync'), true);
  assert.match(await opts.textContent('#schedEmpty'), /還沒有排程/);
});

await step('adding a past schedule is rejected', async () => {
  await opts.fill('#newDate', '2020-01-01');
  await opts.fill('#newTime', '10:00:00');
  await opts.click('#addSched');
  assert.match(await opts.textContent('#addMsg'), /已經過了/);
});

await step('scheduled run: opens page, waits for site time, reloads, claims all', async () => {
  await setStore({ settings: { leadSec: 6, durationSec: 15, refreshIntervalMs: 1000, clickIntervalMs: 300 } });
  // Target 10 s from now on the *site's* clock, whole second.
  const target = Math.ceil((serverNow() + 10000) / 1000) * 1000;
  resetSite(target);
  const d = new Date(target);
  await opts.selectOption('#newMode', 'once');
  await opts.fill('#newName', '測試券');
  await opts.fill('#newDate', `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`);
  await opts.fill('#newTime', `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`);
  await opts.click('#addSched');
  assert.match(await opts.textContent('#addMsg'), /已新增/);
  await waitFor(async () => (await store('upcoming')).upcoming && Object.keys((await store('upcoming')).upcoming).length, 3000, 'upcoming');
  assert.equal(await opts.locator('#schedList tr').count(), 1);

  // Page opens ~6 s before the target.
  await waitFor(async () => (await store('run')).run, 9000, 'run to start');
  const { run } = await store('run');
  assert.equal(run.target, target);
  await waitFor(async () => !(await store('run')).run, 25000, 'run to finish');

  assert.ok(site.claimed.A && site.claimed.B, `claimed: ${JSON.stringify(site.claimed)}`);
  assert.equal(site.claimed.C, undefined);
  assert.equal(site.early, 0, 'no claim attempts before open');
  // Claims land right after open on the site's clock (local clock is 1.5 s behind).
  for (const id of ['A', 'B']) {
    const lag = site.claimed[id] - target;
    assert.ok(lag >= 0 && lag < 1500, `claim ${id} lag ${lag} ms; loads ${site.loads.map((t) => t - target)}; ${(await store('log')).log.map((e) => e.msg).join(' | ')}`);
  }
  // One page load from opening the tab, one refresh at the target time.
  assert.ok(site.loads.some((t) => Math.abs(t - target) < 400), `loads: ${site.loads.map((t) => t - target)}`);
  const log = (await store('log')).log.map((e) => e.msg).join('\n');
  assert.match(log, /網站時間校正：電腦時間慢 1[45]\d\d ms/);
  assert.match(log, /優惠券領取完成/);
  assert.match(log, /領取成功/);
  assert.match(log, /確定要領取嗎/);
});

await step('once schedule does not fire again; popup shows no next run', async () => {
  const { upcoming } = await store('upcoming');
  assert.deepEqual(upcoming, {});
  const popup = await context.newPage();
  await popup.goto(`chrome-extension://${extId}/popup.html`);
  assert.equal(await popup.textContent('#big'), '沒有排程');
  await popup.close();
});

const couponTab = () => context.pages().find((p) => p.url().includes('/mem/GetCoupon'));
async function waitForTab() {
  await waitFor(async () => couponTab(), 5000, 'coupon tab');
  return couponTab();
}

await step('start now + coupon filter: only the matching coupon is claimed', async () => {
  await setStore({ settings: { leadSec: 6, durationSec: 12, refreshIntervalMs: 1000, clickIntervalMs: 300, couponFilter: '免運' } });
  resetSite(0);
  await opts.evaluate(() => chrome.runtime.sendMessage({ type: 'startNow', delayMs: 2000 }));
  await waitFor(async () => !(await store('run')).run, 20000, 'run to finish');
  assert.deepEqual(Object.keys(site.claimed), ['A'], JSON.stringify({ claimed: site.claimed, log: (await store('log')).log.slice(0, 6).map((e) => e.msg) }));
  assert.match(await lastLog(), /領取完成/);
  const tabs = context.pages().filter((p) => p.url().includes('/mem/GetCoupon'));
  assert.equal(tabs.length, 1, 'reuses the existing coupon tab');
  // Dialog hooks are off again outside a run.
  const tab = couponTab();
  const flag = await tab.evaluate(() => sessionStorage.getItem('__couponAutoRun'));
  assert.equal(flag, null);
});

await step('reloads until the button shows up', async () => {
  await setStore({ settings: { leadSec: 6, durationSec: 15, refreshIntervalMs: 1000, clickIntervalMs: 300, refreshAtStart: false } });
  resetSite(serverNow() + 4500); // opens 2.5 s after the run's target
  await opts.evaluate(() => chrome.runtime.sendMessage({ type: 'startNow', delayMs: 2000 }));
  await waitFor(async () => !(await store('run')).run, 20000, 'run to finish');
  assert.ok(site.claimed.A && site.claimed.B, JSON.stringify(site.claimed));
  assert.ok(site.loads.length >= 3, `loads ${site.loads.length}`);
});

await step('stop button ends the run and removes the overlay', async () => {
  await setStore({ settings: { leadSec: 6, durationSec: 30 } });
  resetSite(Infinity);
  await opts.evaluate(() => chrome.runtime.sendMessage({ type: 'startNow', delayMs: 20000 }));
  await waitFor(async () => (await store('run')).run, 3000, 'run');
  const tab = couponTab();
  await tab.waitForSelector('#__coupon_overlay', { timeout: 5000 });
  await tab.locator('#__coupon_overlay button').click();
  await waitFor(async () => !(await store('run')).run, 3000, 'run cleared');
  await waitFor(async () => !(await tab.$('#__coupon_overlay')), 3000, 'overlay removed');
  assert.match(await lastLog(), /已停止/);
});

await step('login redirect is reported', async () => {
  await setStore({ settings: { leadSec: 6, durationSec: 15 } });
  resetSite(Infinity);
  site.loginRequired = true;
  await opts.evaluate(() => chrome.runtime.sendMessage({ type: 'startNow', delayMs: 3000 }));
  await waitFor(async () => !(await store('run')).run, 8000, 'run to finish');
  assert.match(await lastLog(), /需要先登入/);
});


// ------------------------------------------- realistic myship page ----
await step('myship page: AJAX list, pagination, both tabs, WaitAndReload, dialogs', async () => {
  mode = 'myship';
  await setStore({ schedules: [], settings: { leadSec: 6, durationSec: 25, refreshIntervalMs: 1000, clickIntervalMs: 300, couponTabs: 'both' } });
  for (const p of context.pages()) if (p.url().includes('/mem/GetCoupon')) await p.close();
  const target = Math.ceil((serverNow() + 9000) / 1000) * 1000;
  myship.reset(target);
  const d = new Date(target);
  await setStore({ schedules: [{ id: 'm1', name: '雙11', mode: 'once', enabled: true,
    date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
    time: `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` }] });
  await waitFor(async () => (await store('run')).run, 9000, 'run to start');
  const tab = await waitForTab();
  const loadsBefore = myship.st.listRequests.length;
  await waitFor(async () => !(await store('run')).run, 40000, 'run to finish');
  const st = myship.st;
  const log = (await store('log')).log.map((e) => e.msg).join('\n');
  const info = JSON.stringify({ claimed: Object.keys(st.claimed), early: st.early, reqs: st.listRequests.map((r) => `${r.view[3]}${r.pgidx}/${r.pgsize}@${r.at - target}`) }) + '\n' + log.slice(0, 1500);
  assert.deepEqual(Object.keys(st.claimed).sort(), ['A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'S1'], info);
  assert.equal(st.early, 0, info);
  // The list was reloaded over AJAX (24 per page), not by reloading the page.
  assert.ok(st.listRequests.some((r) => r.pgsize === 24 && Math.abs(r.at - target) < 400), info);
  assert.ok(st.listRequests.some((r) => r.pgidx === 2), 'visited page 2: ' + info);
  assert.ok(st.listRequests.some((r) => r.view === 'getSellerCoupon'), 'visited seller tab: ' + info);
  assert.ok(st.listRequests.length > loadsBefore);
  const first = Math.min(...Object.values(st.claimed)) - target;
  assert.ok(first >= 0 && first < 1000, `first claim ${first} ms after open; ${info}`);
  assert.equal(await tab.evaluate(() => window.__crumb || 0), 0, 'breadcrumb link must not be clicked');
  assert.match(log, /優惠券領取完成/);
  assert.match(log, /領取成功/);
  assert.match(log, /人數眾多/);
  // Dialogs were dismissed.
  assert.equal(await tab.locator('.alertify').count(), 0);
  assert.equal(await tab.$eval('#getTicketDialog', (e) => getComputedStyle(e).display), 'none');
});

await step('myship page: coupon filter claims only the matching card', async () => {
  await setStore({ schedules: [], settings: { leadSec: 6, durationSec: 15, refreshIntervalMs: 1000, clickIntervalMs: 300, couponTabs: 'getAllCoupon', couponFilter: '折35元' } });
  myship.reset(0);
  await opts.evaluate(() => chrome.runtime.sendMessage({ type: 'startNow', delayMs: 3000 }));
  await waitFor(async () => !(await store('run')).run, 25000, 'run to finish');
  assert.deepEqual(Object.keys(myship.st.claimed), ['A1'], JSON.stringify(myship.st));
  assert.match(await lastLog(), /領取完成/);
});

await step('myship page: no coupons open -> list refreshed, then nobutton', async () => {
  await setStore({ settings: { leadSec: 6, durationSec: 6, refreshIntervalMs: 1000, couponTabs: 'getAllCoupon' } });
  myship.reset(Infinity);
  const before = myship.st.listRequests.length;
  await opts.evaluate(() => chrome.runtime.sendMessage({ type: 'startNow', delayMs: 3000 }));
  await waitFor(async () => !(await store('run')).run, 25000, 'run to finish');
  assert.ok(myship.st.listRequests.length - before >= 4, `list requests ${myship.st.listRequests.length - before}`);
  assert.match(await lastLog(), /沒有找到領取按鈕/);
  mode = 'simple';
});

await step('daily schedule reschedules for tomorrow', async () => {
  await setStore({ schedules: [{ id: 'd1', name: '', mode: 'daily', time: '00:00:00', days: [], enabled: true }] });
  await waitFor(async () => (await store('upcoming')).upcoming?.d1, 3000, 'upcoming');
  const { upcoming } = await store('upcoming');
  const t = new Date(upcoming.d1);
  assert.equal(t.getHours(), 0);
  assert.ok(upcoming.d1 > Date.now() && upcoming.d1 - Date.now() <= 86400000);
  const alarms = await opts.evaluate(() => chrome.alarms.getAll());
  assert.ok(alarms.some((a) => a.name === `sched:d1:${upcoming.d1}`));
});

await context.close();
console.log(failures ? `\n${failures} failed` : '\nall passed');
process.exit(failures ? 1 : 0);
