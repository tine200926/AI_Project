// node --test tests/core.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const C = require('../lib/core.js');
const D = C.sanitizeSettings(null);

test('claim text: keywords, excludes, long text', () => {
  assert.equal(C.isClaimText('領取', D), true);
  assert.equal(C.isClaimText(' 立即 領取 ', D), true);
  assert.equal(C.isClaimText('已領取', D), false);
  assert.equal(C.isClaimText('已領完', D), false);
  assert.equal(C.isClaimText('即將開放', D), false);
  assert.equal(C.isClaimText('查看詳情', D), false);
  assert.equal(C.isClaimText('滿299折50 限時領取 使用期限 2026/11/11 至 2026/11/30', D), false);
  // Custom-selector mode: anything not excluded.
  assert.equal(C.isClaimText('GET', D, false), true);
  assert.equal(C.isClaimText('已領取', D, false), false);
});

test('coupon filter', () => {
  assert.equal(C.matchesFilter('免運券 滿 0 元', D), true);
  const f = { ...D, couponFilter: '免運，50元' };
  assert.equal(C.matchesFilter('免 運券', f), true);
  assert.equal(C.matchesFilter('折抵 50元', f), true);
  assert.equal(C.matchesFilter('9折券', f), false);
});

test('settings are clamped', () => {
  const s = C.sanitizeSettings({ clickIntervalMs: 1, leadSec: '30', timeSync: 0, bogus: 1, couponFilter: 5 });
  assert.equal(s.clickIntervalMs, 100);
  assert.equal(s.leadSec, 30);
  assert.equal(s.timeSync, false);
  assert.equal(s.couponFilter, '5');
  assert.equal('bogus' in s, false);
});

test('parseTime / parseDate', () => {
  assert.deepEqual(C.parseTime('10:00'), { h: 10, m: 0, s: 0, ms: 0 });
  assert.deepEqual(C.parseTime('23:59:59.5'), { h: 23, m: 59, s: 59, ms: 500 });
  assert.equal(C.parseTime('24:00'), null);
  assert.equal(C.parseTime('10:60'), null);
  assert.deepEqual(C.parseDate('2026-11-11'), { y: 2026, mo: 11, d: 11 });
  assert.equal(C.parseDate('2026-02-30'), null);
});

test('nextRun: once', () => {
  const s = { mode: 'once', date: '2026-11-11', time: '10:00:00' };
  const at = new Date(2026, 10, 11, 10, 0, 0).getTime();
  assert.equal(C.nextRun(s, at - 1), at);
  assert.equal(C.nextRun(s, at), null);
});

test('nextRun: daily and weekly', () => {
  const s = { mode: 'daily', time: '10:00:00', days: [] };
  const wed9 = new Date(2026, 10, 4, 9, 0).getTime();   // Wed 2026-11-04
  assert.equal(C.nextRun(s, wed9), new Date(2026, 10, 4, 10, 0).getTime());
  const wed11 = new Date(2026, 10, 4, 11, 0).getTime();
  assert.equal(C.nextRun(s, wed11), new Date(2026, 10, 5, 10, 0).getTime());
  const weekly = { mode: 'daily', time: '00:00:00', days: [1] }; // Mondays
  assert.equal(C.nextRun(weekly, wed9), new Date(2026, 10, 9, 0, 0).getTime());
  assert.equal(C.nextRun({ mode: 'daily', time: 'x' }, wed9), null);
});

test('estimateOffset narrows across a second boundary', () => {
  const trueOffset = 2345; // server is 2.345 s ahead
  const samples = [];
  for (let i = 0; i < 12; i++) {
    const send = 1_000_000 + i * 110;
    const recv = send + 40;
    const serverAt = send + 20 + trueOffset;
    samples.push({ send, recv, date: Math.floor(serverAt / 1000) * 1000 });
  }
  const est = C.estimateOffset(samples);
  assert.ok(Math.abs(est.offset - trueOffset) <= est.error, JSON.stringify(est));
  assert.ok(est.error <= 80, JSON.stringify(est));
  assert.equal(C.estimateOffset([]), null);
  assert.equal(C.estimateOffset([{ send: 1, recv: 2, date: NaN }]), null);
});

test('describeSchedule', () => {
  assert.equal(C.describeSchedule({ mode: 'daily', time: '10:00:00', days: [] }), '每天 10:00:00');
  assert.equal(C.describeSchedule({ mode: 'daily', time: '10:00:00', days: [5, 1] }), '每週一、五 10:00:00');
  assert.equal(C.describeSchedule({ mode: 'once', date: '2026-11-11', time: '00:00:00' }), '2026-11-11 00:00:00');
});

test('status text and coupon tabs', () => {
  assert.equal(C.isStatusText('已領取', D), true);
  assert.equal(C.isStatusText('領取', D), true);
  assert.equal(C.isStatusText('使用規則', D), false);
  assert.deepEqual(C.couponViews(D), ['getAllCoupon']);
  assert.deepEqual(C.couponViews(C.sanitizeSettings({ couponTabs: 'both' })), ['getAllCoupon', 'getSellerCoupon']);
  assert.equal(C.sanitizeSettings({ couponTabs: 'nope' }).couponTabs, 'getAllCoupon');
});
