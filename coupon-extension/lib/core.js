// Pure helpers shared by the service worker, content script, popup, options
// page and the node tests. No Chrome APIs, no DOM.
(function (root) {
  'use strict';

  const PAGE_URL = 'https://myship.7-11.com.tw/mem/GetCoupon';

  const DEFAULTS = {
    leadSec: 60,            // open/refresh the coupon page this many seconds early
    durationSec: 60,        // keep trying this long after the target time
    clickIntervalMs: 500,   // re-click the same button no faster than this
    refreshIntervalMs: 3000, // reload when no claim button shows up for this long
    advanceMs: 0,           // fire this many ms before the target (network latency)
    refreshAtStart: true,   // reload the page exactly at the target time
    timeSync: true,         // correct the local clock with the site's Date header
    autoConfirm: true,      // accept confirm()/alert() and click "確定" in popups
    claimKeywords: '領取,立即領取,領券,領取優惠券',
    excludeKeywords: '已領取,已領完,已兌換,領取完畢,已結束,已使用,已過期,已失效,已額滿,即將開放,尚未開始,未開放',
    couponFilter: '',       // only coupons whose card text contains one of these
    customSelector: '',     // CSS selector for the claim button (overrides keywords)
    couponTabs: 'getAllCoupon', // myship tabs to sweep: getAllCoupon | getSellerCoupon | both
  };

  const ENUMS = { couponTabs: ['getAllCoupon', 'getSellerCoupon', 'both'] };

  const LIMITS = {
    leadSec: [5, 600],
    durationSec: [5, 600],
    clickIntervalMs: [100, 10000],
    refreshIntervalMs: [500, 60000],
    advanceMs: [0, 5000],
  };

  function sanitizeSettings(input) {
    const out = { ...DEFAULTS };
    for (const [k, def] of Object.entries(DEFAULTS)) {
      if (!input || input[k] === undefined || input[k] === null) continue;
      const v = input[k];
      if (typeof def === 'number') {
        const n = Number(v);
        if (!Number.isFinite(n)) continue;
        const [lo, hi] = LIMITS[k];
        out[k] = Math.min(hi, Math.max(lo, Math.round(n)));
      } else if (typeof def === 'boolean') {
        out[k] = !!v;
      } else if (ENUMS[k]) {
        if (ENUMS[k].includes(v)) out[k] = v;
      } else {
        out[k] = String(v);
      }
    }
    return out;
  }

  function splitList(s) {
    return String(s || '').split(/[,，、;；\n]/).map((x) => x.trim()).filter(Boolean);
  }

  function norm(s) {
    return String(s || '').replace(/\s+/g, '').toLowerCase();
  }

  // Is `text` (a button's label) something we should click?
  // `useKeywords: false` (custom selector mode) only applies the exclude list.
  function isClaimText(text, settings, useKeywords = true) {
    const t = norm(text);
    if (useKeywords && (!t || t.length > 20)) return false;
    if (splitList(settings.excludeKeywords).some((k) => t.includes(norm(k)))) return false;
    if (!useKeywords) return true;
    return splitList(settings.claimKeywords).some((k) => t.includes(norm(k)));
  }

  // A short label that is either a claim button or one of the excluded
  // states (已領取…) — i.e. the status button of some coupon card.
  function isStatusText(text, settings) {
    const t = norm(text);
    if (!t || t.length > 20) return false;
    return [...splitList(settings.claimKeywords), ...splitList(settings.excludeKeywords)].some((k) => t.includes(norm(k)));
  }

  function couponViews(settings) {
    if (settings.couponTabs === 'both') return ['getAllCoupon', 'getSellerCoupon'];
    return [settings.couponTabs === 'getSellerCoupon' ? 'getSellerCoupon' : 'getAllCoupon'];
  }

  function matchesFilter(cardText, settings) {
    const words = splitList(settings.couponFilter);
    if (!words.length) return true;
    const t = norm(cardText);
    return words.some((w) => t.includes(norm(w)));
  }

  // "10:00", "10:00:05", "10:00:05.250" -> { h, m, s, ms }
  function parseTime(str) {
    const m = /^\s*(\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?\s*$/.exec(String(str || ''));
    if (!m) return null;
    const h = +m[1], mi = +m[2], s = +(m[3] || 0), ms = +((m[4] || '0').padEnd(3, '0'));
    if (h > 23 || mi > 59 || s > 59) return null;
    return { h, m: mi, s, ms };
  }

  function parseDate(str) {
    const m = /^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$/.exec(String(str || ''));
    if (!m) return null;
    const y = +m[1], mo = +m[2], d = +m[3];
    const dt = new Date(y, mo - 1, d);
    if (dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== d) return null;
    return { y, mo, d };
  }

  // Schedules (all times are the computer's local time):
  //   { mode: 'once',  date: 'YYYY-MM-DD', time: 'HH:MM:SS' }
  //   { mode: 'daily', time: 'HH:MM:SS', days: [0..6] }  // 0 = Sunday, [] = every day
  // Returns the first run strictly after `after` (ms), or null.
  function nextRun(schedule, after) {
    const t = parseTime(schedule && schedule.time);
    if (!t) return null;
    if (schedule.mode === 'once') {
      const d = parseDate(schedule.date);
      if (!d) return null;
      const at = new Date(d.y, d.mo - 1, d.d, t.h, t.m, t.s, t.ms).getTime();
      return at > after ? at : null;
    }
    const days = Array.isArray(schedule.days) ? schedule.days.map(Number) : [];
    const base = new Date(after);
    for (let i = 0; i <= 8; i++) {
      const at = new Date(base.getFullYear(), base.getMonth(), base.getDate() + i, t.h, t.m, t.s, t.ms);
      if (at.getTime() <= after) continue;
      if (days.length && !days.includes(at.getDay())) continue;
      return at.getTime();
    }
    return null;
  }

  function validateSchedule(s) {
    if (!parseTime(s.time)) return '時間格式要像 10:00:00';
    if (s.mode === 'once') {
      if (!parseDate(s.date)) return '請選日期';
    } else if (s.mode !== 'daily') {
      return '不明的排程類型';
    }
    return '';
  }

  // Estimate (server clock - local clock) from HEAD responses.
  // Each sample: { send, recv } local ms and `date` = Date header in ms
  // (1-second resolution). The server's clock read some instant in
  // [send, recv] and showed a value in [date, date + 1000), so each sample
  // bounds the offset; samples that straddle a second boundary tighten it.
  function estimateOffset(samples) {
    let lo = -Infinity, hi = Infinity;
    let used = 0;
    for (const s of samples || []) {
      if (!Number.isFinite(s.date) || !(s.recv >= s.send)) continue;
      lo = Math.max(lo, s.date - s.recv);
      hi = Math.min(hi, s.date + 1000 - s.send);
      used++;
    }
    if (!used || lo > hi) return null;
    return { offset: Math.round((lo + hi) / 2), error: Math.round((hi - lo) / 2), samples: used };
  }

  function pad(n, w = 2) { return String(n).padStart(w, '0'); }

  function fmtDateTime(ms, withMs = false) {
    const d = new Date(ms);
    const s = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    return withMs ? `${s}.${pad(d.getMilliseconds(), 3)}` : s;
  }

  function fmtCountdown(ms) {
    if (ms < 0) ms = 0;
    const total = Math.floor(ms / 1000);
    const d = Math.floor(total / 86400);
    const h = Math.floor((total % 86400) / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const core = `${pad(h)}:${pad(m)}:${pad(s)}`;
    return d ? `${d} 天 ${core}` : core;
  }

  const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六'];

  function describeSchedule(s) {
    if (s.mode === 'once') return `${s.date} ${s.time}`;
    const days = Array.isArray(s.days) ? s.days.map(Number).sort() : [];
    if (!days.length || days.length === 7) return `每天 ${s.time}`;
    return `每週${days.map((d) => WEEKDAYS[d]).join('、')} ${s.time}`;
  }

  const api = {
    PAGE_URL, DEFAULTS, LIMITS, WEEKDAYS,
    sanitizeSettings, splitList, norm, isClaimText, isStatusText, couponViews, matchesFilter,
    parseTime, parseDate, nextRun, validateSchedule, estimateOffset,
    fmtDateTime, fmtCountdown, describeSchedule,
  };
  root.CouponCore = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
