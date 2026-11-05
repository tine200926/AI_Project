// Runs on myship.7-11.com.tw pages. When the service worker has started a
// run for this tab it waits for the target time (corrected with the site's
// clock), reloads, then clicks every claim button until they are all claimed
// or the run times out.
(() => {
  'use strict';
  if (window.top !== window) return;
  const C = globalThis.CouponCore;
  const FLAG = '__couponAutoRun';
  const REFRESHED = '__couponRefreshedAt';
  const DIALOG_OK = ['確定', '確認', 'ok', '好', '好的', '我知道了', '知道了', '關閉', '返回'];
  const DIALOG_SEL = '[role="dialog"],[role="alertdialog"],.modal,.swal2-popup,.swal-modal,.layui-layer,.el-message-box,.ant-modal,[class*="modal"],[class*="Modal"],[class*="dialog"],[class*="Dialog"],[class*="popup"],[class*="Popup"],[class*="alert"],[class*="lightbox"]';
  const BTN_SEL = 'button,a,input[type="button"],input[type="submit"],[role="button"],[onclick],[class*="btn"],[class*="Btn"],[class*="button"],[class*="Button"]';

  let run = null;
  let settings = C.sanitizeSettings(null);
  let myTabId = null;
  let stopped = false;
  let timers = [];
  let ui = null;
  const seenMessages = new Set();

  const later = (fn, ms) => { const t = setTimeout(fn, ms); timers.push(t); return t; };
  const send = (msg) => chrome.runtime.sendMessage(msg).catch(() => null);
  const patch = (p) => send({ type: 'runPatch', id: run.id, patch: p });
  const offset = () => (settings.timeSync && Number.isFinite(run && run.offset) ? run.offset : 0);
  // Local time at which the server clock reads `run.target - advanceMs`.
  const fireAtLocal = () => run.target - settings.advanceMs - offset();

  // ------------------------------------------------------------- overlay --
  function overlay() {
    if (ui) return ui;
    const host = document.createElement('div');
    host.id = '__coupon_overlay';
    host.style.cssText = 'position:fixed;top:12px;right:12px;z-index:2147483647;';
    const root = host.attachShadow({ mode: 'open' });
    root.innerHTML = `<style>
      .box{font:13px/1.45 system-ui,"Microsoft JhengHei",sans-serif;background:#1f2328;color:#fff;border-radius:10px;
        padding:10px 12px;min-width:220px;box-shadow:0 4px 18px rgba(0,0,0,.3)}
      .t{font-weight:600;margin-bottom:2px}.big{font-size:20px;font-variant-numeric:tabular-nums;margin:2px 0}
      .s{opacity:.8;font-size:12px}button{margin-top:6px;font:inherit;background:#c5221f;color:#fff;border:0;border-radius:6px;padding:3px 10px;cursor:pointer}
    </style><div class="box"><div class="t"></div><div class="big"></div><div class="s"></div><button type="button">停止</button></div>`;
    root.querySelector('button').addEventListener('click', () => send({ type: 'stop' }));
    (document.body || document.documentElement).appendChild(host);
    ui = { host, title: root.querySelector('.t'), big: root.querySelector('.big'), sub: root.querySelector('.s') };
    return ui;
  }
  function show(title, big, sub) {
    const o = overlay();
    o.title.textContent = `優惠券定時領取｜${title}`;
    o.big.textContent = big;
    o.sub.textContent = sub;
  }

  function cleanup() {
    stopped = true;
    timers.forEach(clearTimeout);
    timers = [];
    try { sessionStorage.removeItem(FLAG); sessionStorage.removeItem(REFRESHED); } catch (e) { /* ignore */ }
    if (ui) { ui.host.remove(); ui = null; }
  }

  function finish(result, detail) {
    if (stopped) return;
    const id = run.id;
    cleanup();
    send({ type: 'runFinish', id, result, detail });
  }

  // ------------------------------------------------------------ buttons --
  function visible(el) {
    if (!el.getClientRects().length) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity) > 0.05;
  }
  function disabled(el) {
    const cls = el.getAttribute('class') || '';
    return !!el.disabled || el.getAttribute('aria-disabled') === 'true' || /(^|[\s_-])disabled?([\s_-]|$)/i.test(cls);
  }
  function label(el) {
    return (el.tagName === 'INPUT' ? el.value : (el.innerText || el.textContent || '')) || '';
  }
  // Innermost candidates only: <a class="btn"><span onclick>領取</span></a> counts once.
  function innermost(list) {
    return list.filter((el) => !list.some((o) => o !== el && el.contains(o)));
  }
  // The nearest ancestor that holds this button and no other button — i.e.
  // the coupon's own card — for the coupon-name filter. `all` must include
  // the already-claimed buttons too, or a card would grow past its neighbour.
  function cardText(el, all) {
    let card = el;
    for (let p = el.parentElement, i = 0; p && i < 8; p = p.parentElement, i++) {
      if (all.some((o) => o !== el && !el.contains(o) && !o.contains(el) && p.contains(o))) break;
      card = p;
    }
    return card.innerText || card.textContent || '';
  }
  function insideDialog(el) {
    return !!el.closest(DIALOG_SEL);
  }

  function findClaimButtons() {
    const custom = settings.customSelector.trim();
    let list;
    try {
      list = Array.from(document.querySelectorAll(custom || BTN_SEL));
    } catch (e) {
      list = Array.from(document.querySelectorAll(BTN_SEL));
    }
    list = list.filter((el) => el.id !== '__coupon_overlay' && visible(el));
    if (!custom) list = list.filter((el) => !insideDialog(el));
    const textual = innermost(list.filter((el) => C.isClaimText(label(el), settings, !custom)));
    if (!C.splitList(settings.couponFilter).length) return textual.filter((el) => !disabled(el));
    const all = custom ? list : list.filter((el) => label(el).trim().length <= 20);
    return textual.filter((el) => !disabled(el) && C.matchesFilter(cardText(el, all), settings));
  }

  function handleDialogs() {
    const dialogs = Array.from(document.querySelectorAll(DIALOG_SEL)).filter(visible);
    for (const d of innermost(dialogs)) {
      const text = (d.innerText || '').trim().replace(/\s+/g, ' ');
      if (text) note(text.slice(0, 80));
      if (!settings.autoConfirm) continue;
      const btn = Array.from(d.querySelectorAll('button,a,input[type="button"],input[type="submit"],[role="button"],[class*="btn"]'))
        .filter(visible)
        .find((b) => DIALOG_OK.includes(C.norm(label(b))));
      if (btn) btn.click();
    }
  }

  function note(msg) {
    msg = String(msg || '').trim();
    if (!msg || seenMessages.has(msg)) return;
    seenMessages.add(msg);
    patch({ message: msg });
  }

  document.addEventListener('__coupon_dialog', (e) => {
    try { note(JSON.parse(e.detail).msg); } catch (err) { /* ignore */ }
  });

  // ---------------------------------------------------------- time sync --
  async function syncClock() {
    const samples = [];
    for (let i = 0; i < 12 && !stopped; i++) {
      const send0 = Date.now();
      try {
        const r = await fetch(`${location.pathname}?_ts=${send0}`, { method: 'HEAD', cache: 'no-store', credentials: 'include' });
        const recv = Date.now();
        const date = Date.parse(r.headers.get('date') || '');
        samples.push({ send: send0, recv, date });
      } catch (e) { /* ignore */ }
      await new Promise((r) => setTimeout(r, 90));
    }
    const est = C.estimateOffset(samples);
    if (est && Math.abs(est.offset) < 10 * 60 * 1000) {
      run.offset = est.offset;
      await patch({ offset: est.offset });
      send({ type: 'log', msg: `網站時間校正：電腦時間${est.offset >= 0 ? '慢' : '快'} ${Math.abs(est.offset)} ms（誤差 ±${est.error} ms）` });
    } else {
      run.offset = 0;
      await patch({ offset: 0 });
    }
  }

  // ---------------------------------------------------------- run phases --
  function waitUntil(localMs, fn) {
    const tick = () => {
      if (stopped) return;
      const left = localMs - Date.now();
      if (left <= 0) return fn();
      const sub = settings.timeSync && run.offset ? `已校正網站時間 ${run.offset > 0 ? '+' : ''}${run.offset} ms` : '使用電腦時間';
      show('等待中', C.fmtCountdown(left + 999), `目標 ${C.fmtDateTime(run.target, true)}｜${sub}`);
      if (left > 1000) later(tick, Math.min(250, left - 900));
      else if (left > 20) later(tick, left - 15);
      else {
        while (Date.now() < localMs) { /* spin the last few ms */ }
        fn();
      }
    };
    tick();
  }

  function reload(reason) {
    if (stopped) return;
    patch({ addReloads: 1, status: reason });
    location.reload();
  }

  function claimLoop() {
    if (stopped) return;
    try { sessionStorage.setItem(FLAG, run.id); } catch (e) { /* ignore */ }
    const lastClick = new WeakMap();
    let clicks = run.clicks || 0;
    let pending = 0;
    let readyAt = document.readyState === 'complete' ? Date.now() : Infinity;
    let emptySince = 0;
    if (readyAt === Infinity) window.addEventListener('load', () => { readyAt = Date.now(); }, { once: true });
    let flushAt = 0;

    const tick = () => {
      if (stopped) return;
      const now = Date.now();
      if (now > run.end) {
        if (pending) patch({ addClicks: pending });
        return finish(clicks + pending ? 'timeout' : 'nobutton', clicks + pending ? '' : '時間內沒有出現可以按的領取按鈕');
      }
      handleDialogs();
      const btns = findClaimButtons();
      if (btns.length) {
        emptySince = 0;
        for (const b of btns) {
          if (now - (lastClick.get(b) || 0) < settings.clickIntervalMs) continue;
          lastClick.set(b, now);
          b.click();
          pending++;
        }
      } else if (!emptySince) {
        emptySince = now;
      }
      if (pending && now >= flushAt) {
        patch({ addClicks: pending, status: 'clicking' });
        clicks += pending;
        pending = 0;
        flushAt = now + 400;
      }
      show('領取中', `已點擊 ${clicks + pending} 次`, `${btns.length} 個可領取按鈕｜剩 ${Math.ceil((run.end - now) / 1000)} 秒`);

      if (!btns.length && now - readyAt > 1500) {
        if (clicks + pending > 0 && now - emptySince > 1500) {
          // Every button we clicked turned into 已領取 (or vanished).
          if (pending) patch({ addClicks: pending });
          return finish('done', '所有領取按鈕都已按過');
        }
        if (clicks + pending === 0 && now - readyAt > settings.refreshIntervalMs) {
          return reload('waiting-for-button');
        }
      }
      later(tick, 100);
    };
    tick();
  }

  async function begin() {
    if (/login|signin/i.test(location.pathname + location.search)) {
      return finish('login', '請先在這個分頁登入，再重新設定排程或按「立即執行一次」');
    }
    if (!/\/mem\/GetCoupon/i.test(location.pathname)) {
      show('前往優惠券頁', '', location.pathname);
      later(() => { location.href = C.PAGE_URL; }, 500);
      return;
    }
    const fire = () => {
      let refreshed = '';
      try { refreshed = sessionStorage.getItem(REFRESHED); } catch (e) { /* ignore */ }
      if (settings.refreshAtStart && refreshed !== run.id) {
        try { sessionStorage.setItem(REFRESHED, run.id); sessionStorage.setItem(FLAG, run.id); } catch (e) { /* ignore */ }
        return reload('refresh-at-start');
      }
      claimLoop();
    };

    if (Date.now() >= fireAtLocal()) return claimLoop();
    show('等待中', '', '準備中…');
    if (settings.timeSync && run.offset === undefined && run.target - Date.now() > 2500) await syncClock();
    if (stopped) return;
    // Loaded long before the target: refresh at the target to get the live page.
    waitUntil(fireAtLocal(), fire);
  }

  async function init() {
    const res = await send({ type: 'whoami' });
    myTabId = res && res.tabId;
    for (let i = 0; i < 20; i++) {
      const d = await chrome.storage.local.get(['run', 'settings']);
      run = d.run || null;
      settings = C.sanitizeSettings(d.settings);
      if (!run || Date.now() > run.end) return;
      if (run.tabId === myTabId) break;
      if (run.tabId !== null) return; // a run for another tab
      await new Promise((r) => setTimeout(r, 150));
    }
    if (!run || run.tabId !== myTabId) return;

    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== 'local') return;
      if (changes.settings) settings = C.sanitizeSettings(changes.settings.newValue);
      if (changes.run) {
        const nv = changes.run.newValue;
        if (!nv || nv.id !== run.id) cleanup();
        else run = { ...nv, offset: run.offset ?? nv.offset };
      }
    });
    begin();
  }

  init();
})();
