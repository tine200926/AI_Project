// Runs on myship.7-11.com.tw pages. When the service worker has started a
// run for this tab it waits for the target time (corrected with the site's
// clock), refreshes the coupon list, then clicks every claim button until
// they are all claimed or the run times out.
//
// On the real GetCoupon page the list is loaded over AJAX into #tabPanel,
// so refreshing means asking the page's own loadCouponView() (via hook.js)
// — no full reload. On any other page layout it falls back to
// location.reload().
(() => {
  'use strict';
  if (window.top !== window) return;
  const C = globalThis.CouponCore;
  const FLAG = '__couponAutoRun';
  const REFRESHED = '__couponRefreshedAt';
  const PAGE_SIZE = 24; // the largest the page offers
  const DIALOG_OK = ['確定', '確認', 'ok', '好', '好的', '我知道了', '知道了', '關閉', '返回'];
  const DIALOG_SEL = '[role="dialog"],[role="alertdialog"],.modal,.alertify,.swal2-popup,.swal-modal,.layui-layer,.el-message-box,.ant-modal,[class*="modal"],[class*="Modal"],[class*="dialog"],[class*="Dialog"],[class*="popup"],[class*="Popup"],[class*="alert"],[class*="lightbox"]';
  const DIALOG_CLOSE_SEL = '[data-dismiss="modal"],button.close,[aria-label="Close"]';
  // myship's own claim buttons: <button id="GetCouponTicket_xxx" onclick="getCouponTicket(...)">
  const SITE_CLAIM_SEL = '[id^="GetCouponTicket_"],[onclick*="getCouponTicket"]';
  const BTN_SEL = `button,a,input[type="button"],input[type="submit"],[role="button"],[onclick],[class*="btn"],[class*="Btn"],[class*="button"],[class*="Button"],${SITE_CLAIM_SEL}`;
  // Never click these: menus, breadcrumb (its 「領取優惠券」 is a link), dialogs.
  const SKIP_SEL = `#__coupon_overlay,nav,.breadcrumb,.main-menu,header,footer,.nav-pills,${DIALOG_SEL}`;
  const BUSY_SEL = '#divLoadingContent,#divLoadingCover';

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
  const synced = () => settings.timeSync && Number.isFinite(run && run.offset);
  const offset = () => (synced() ? run.offset : 0);
  // Local time at which the server clock reads `run.target - advanceMs`.
  // Aim at the late edge of the clock estimate: a refresh that lands a few
  // ms before the coupon opens costs a whole refresh interval.
  const fireAtLocal = () => run.target - settings.advanceMs - offset() + (synced() ? (run.offsetError || 0) : 0);
  // Refresh faster right after the target, when the coupon is most likely
  // to appear any moment.
  const refreshEvery = (now) => (now - fireAtLocal() < 3000 ? Math.min(settings.refreshIntervalMs, 500) : settings.refreshIntervalMs);

  // ------------------------------------------------------------- bridge --
  function bridge(cmd) {
    const html = document.documentElement;
    html.removeAttribute('data-coupon-cmd-result');
    document.dispatchEvent(new CustomEvent('__coupon_cmd', { detail: JSON.stringify(cmd) }));
    return html.getAttribute('data-coupon-cmd-result') === 'ok';
  }
  const hasBridge = () => bridge({ cmd: 'ping' });

  let listRequestedAt = 0;
  // Ask the page to load one page of one tab, and mark the current list
  // stale so listReady() can tell when the new one has arrived.
  function requestList(view, pageIndex) {
    const panel = document.getElementById('tabPanel');
    if (panel) for (const ch of panel.children) ch.setAttribute('data-coupon-stale', '1');
    listRequestedAt = Date.now();
    return bridge({ cmd: 'loadList', viewType: view, pageSize: PAGE_SIZE, pageIndex });
  }
  function listReady() {
    const panel = document.getElementById('tabPanel');
    if (!panel) return true;
    const fresh = panel.children.length > 0 && !panel.querySelector(':scope > [data-coupon-stale]');
    if (fresh && !siteBusy()) return true;
    return Date.now() - listRequestedAt > 8000; // request failed: use what is there
  }
  function pageInfo() {
    const num = (id) => Number((document.querySelector(`#tabPanel #${id}`) || {}).value) || 1;
    return { pgidx: num('pgidx'), pgcnt: num('pgcnt') };
  }

  let busySince = 0;
  // The site's "資料處理中" cover — don't fire more requests while it's up.
  function siteBusy() {
    const busy = Array.from(document.querySelectorAll(BUSY_SEL)).some(visible);
    if (!busy) { busySince = 0; return false; }
    if (!busySince) busySince = Date.now();
    return Date.now() - busySince < 10000; // stuck cover: ignore it
  }

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
    const onclick = (el.getAttribute('onclick') || '').replace(/\s+/g, '');
    return !!el.disabled || el.getAttribute('aria-disabled') === 'true' ||
      /(^|[\s_-])disabled?([\s_-]|$)/i.test(cls) || /^returnfalse;?$/i.test(onclick);
  }
  function label(el) {
    return (el.tagName === 'INPUT' ? el.value : (el.innerText || el.textContent || '')) || '';
  }
  // Innermost elements only: <a class="btn"><span onclick>領取</span></a> counts once.
  function innermost(list) {
    return list.filter((el) => !list.some((o) => o !== el && el.contains(o)));
  }
  // Text of the coupon's own card, for the coupon-name filter: myship's
  // `.cp`, or else the largest ancestor holding no other coupon's status
  // button (`others` includes 已領取 buttons, so cards don't grow past them).
  function cardText(el, others) {
    let card = el.closest('.cp');
    if (!card) {
      card = el;
      for (let p = el.parentElement, i = 0; p && i < 8; p = p.parentElement, i++) {
        if (others.some((o) => o !== el && !el.contains(o) && !o.contains(el) && p.contains(o))) break;
        card = p;
      }
    }
    return card.innerText || card.textContent || '';
  }

  // { eligible: claim buttons to click now, waiting: claim buttons the site
  // has locked (e.g. its 20-second WaitAndReload countdown) }
  function scan() {
    const root = document.getElementById('tabPanel') || document;
    const custom = settings.customSelector.trim();
    let list;
    try {
      list = Array.from(root.querySelectorAll(custom || BTN_SEL));
    } catch (e) {
      list = Array.from(root.querySelectorAll(BTN_SEL));
    }
    list = list.filter((el) => !el.closest(custom ? '#__coupon_overlay' : SKIP_SEL) && visible(el));
    const site = (el) => el.matches(SITE_CLAIM_SEL);
    const isClaim = (el) => (custom || site(el))
      ? C.isClaimText(label(el), settings, false)
      : C.isClaimText(label(el), settings);
    const claim = innermost(list.filter(isClaim));
    let picked = claim;
    if (C.splitList(settings.couponFilter).length) {
      const others = list.filter((el) => site(el) || C.isStatusText(label(el), settings));
      picked = claim.filter((el) => C.matchesFilter(cardText(el, others), settings));
    }
    return { eligible: picked.filter((el) => !disabled(el)), waiting: picked.filter(disabled) };
  }

  function loginRequired() {
    if (/login|signin/i.test(location.pathname + location.search)) return true;
    const m = document.getElementById('loginModal');
    return !!(m && visible(m));
  }

  function handleDialogs() {
    const dialogs = Array.from(document.querySelectorAll(DIALOG_SEL)).filter((d) => d.id !== 'loginModal' && visible(d));
    // Outermost: the whole dialog, so its text and its buttons are together.
    const outer = dialogs.filter((d) => !dialogs.some((o) => o !== d && o.contains(d)));
    for (const d of outer) {
      const text = (d.innerText || '').trim().replace(/\s+/g, ' ');
      if (!text) continue;
      note(text.slice(0, 80));
      if (!settings.autoConfirm) continue;
      const btns = Array.from(d.querySelectorAll('button,a,input[type="button"],input[type="submit"],[role="button"],[class*="btn"]')).filter(visible);
      const ok = btns.find((b) => DIALOG_OK.includes(C.norm(label(b)))) ||
        btns.find((b) => b.matches(DIALOG_CLOSE_SEL));
      if (ok) ok.click();
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
      run.offsetError = est.error;
      await patch({ offset: est.offset, offsetError: est.error });
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

  // Shared click bookkeeping for both loops.
  function clicker() {
    const lastClick = new Map();
    const st = { clicks: run.clicks || 0, pending: 0, flushAt: 0 };
    const keyOf = (b) => b.id || b.getAttribute('onclick') || b;
    st.click = (btns, now) => {
      for (const b of btns) {
        const k = keyOf(b);
        if (now - (lastClick.get(k) || 0) < settings.clickIntervalMs) continue;
        lastClick.set(k, now);
        b.click();
        st.pending++;
      }
    };
    st.flush = (now, force = false) => {
      if (st.pending && (force || now >= st.flushAt)) {
        patch({ addClicks: st.pending, status: 'clicking' });
        st.clicks += st.pending;
        st.pending = 0;
        st.flushAt = now + 400;
      }
    };
    st.total = () => st.clicks + st.pending;
    return st;
  }

  function timeUp(st) {
    st.flush(0, true);
    const n = st.total();
    finish(n ? 'timeout' : 'nobutton', n ? '' : '時間內沒有出現可以按的領取按鈕');
  }

  // Real myship page: sweep every page of every selected tab over AJAX.
  function claimLoopAjax(alreadyRequested) {
    if (stopped) return;
    try { sessionStorage.setItem(FLAG, run.id); } catch (e) { /* ignore */ }
    const views = C.couponViews(settings);
    const st = clicker();
    let slot = 0;               // index into views
    let sweepFound = false;     // saw a claim button during this sweep
    let sweepStart = Date.now();
    let idleUntil = 0;          // wait before starting the next empty sweep
    if (!alreadyRequested) requestList(views[0], 1);

    const tick = () => {
      if (stopped) return;
      const now = Date.now();
      if (now > run.end) return timeUp(st);
      if (loginRequired()) return finish('login', '請先在這個分頁登入，再重新設定排程或按「立即執行一次」');
      handleDialogs();
      const busy = siteBusy();
      const { eligible, waiting } = listReady() ? scan() : { eligible: null, waiting: [] };

      if (eligible === null) {
        show('領取中', `已點擊 ${st.total()} 次`, '載入優惠券列表…');
      } else if (eligible.length) {
        sweepFound = true;
        if (!busy) st.click(eligible, now);
        show('領取中', `已點擊 ${st.total()} 次`, `${eligible.length} 張可領取｜剩 ${Math.ceil((run.end - now) / 1000)} 秒`);
      } else if (waiting.length && st.total() > 0) {
        // The site locked the button (WaitAndReload) and reloads it itself.
        sweepFound = true;
        show('領取中', `已點擊 ${st.total()} 次`, `網站要求稍候，${waiting.length} 張等待中`);
      } else if (!busy && now >= idleUntil) {
        // Nothing to click here: next page, next tab, or start over.
        const { pgidx, pgcnt } = pageInfo();
        if (pgidx < pgcnt) {
          requestList(views[slot], pgidx + 1);
        } else if (slot + 1 < views.length) {
          slot++;
          requestList(views[slot], 1);
        } else if (sweepFound) {
          slot = 0; sweepFound = false; sweepStart = now;
          requestList(views[0], 1);
        } else if (st.total() > 0) {
          st.flush(now, true);
          return finish('done', '所有領取按鈕都已按過');
        } else {
          // No coupon open yet: refresh the list every refreshIntervalMs.
          const wait = sweepStart + refreshEvery(now) - now;
          if (wait > 0) {
            idleUntil = now + wait;
          } else {
            slot = 0; sweepStart = now;
            patch({ addReloads: 1, status: 'waiting-for-button' });
            requestList(views[0], 1);
          }
        }
        show('領取中', `已點擊 ${st.total()} 次`, `還沒有可領取的券｜剩 ${Math.ceil((run.end - now) / 1000)} 秒`);
      }
      st.flush(now);
      later(tick, 100);
    };
    tick();
  }

  // Any other page layout: click what is there, reload when nothing shows up.
  function claimLoopReload() {
    if (stopped) return;
    try { sessionStorage.setItem(FLAG, run.id); } catch (e) { /* ignore */ }
    const st = clicker();
    let readyAt = document.readyState === 'complete' ? Date.now() : Infinity;
    let emptySince = 0;
    if (readyAt === Infinity) window.addEventListener('load', () => { readyAt = Date.now(); }, { once: true });

    const tick = () => {
      if (stopped) return;
      const now = Date.now();
      if (now > run.end) return timeUp(st);
      handleDialogs();
      const { eligible, waiting } = scan();
      if (eligible.length || waiting.length) {
        emptySince = 0;
        if (!siteBusy()) st.click(eligible, now);
      } else if (!emptySince) {
        emptySince = now;
      }
      st.flush(now);
      show('領取中', `已點擊 ${st.total()} 次`, `${eligible.length} 個可領取按鈕｜剩 ${Math.ceil((run.end - now) / 1000)} 秒`);

      if (!eligible.length && !waiting.length) {
        if (st.total() > 0 && now - readyAt > 1500 && now - emptySince > 1500) {
          // Every button we clicked turned into 已領取 (or vanished).
          st.flush(now, true);
          return finish('done', '所有領取按鈕都已按過');
        }
        if (st.total() === 0 && now - readyAt > refreshEvery(now)) {
          return reload('waiting-for-button');
        }
      }
      later(tick, 100);
    };
    tick();
  }

  async function begin() {
    if (loginRequired()) {
      return finish('login', '請先在這個分頁登入，再重新設定排程或按「立即執行一次」');
    }
    if (!/\/mem\/GetCoupon/i.test(location.pathname)) {
      show('前往優惠券頁', '', location.pathname);
      later(() => { location.href = C.PAGE_URL; }, 500);
      return;
    }
    const ajax = hasBridge();
    const fire = () => {
      if (ajax) {
        // Reload just the coupon list at the target time.
        return claimLoopAjax(settings.refreshAtStart ? requestList(C.couponViews(settings)[0], 1) : true);
      }
      let refreshed = '';
      try { refreshed = sessionStorage.getItem(REFRESHED); } catch (e) { /* ignore */ }
      if (settings.refreshAtStart && refreshed !== run.id) {
        try { sessionStorage.setItem(REFRESHED, run.id); sessionStorage.setItem(FLAG, run.id); } catch (e) { /* ignore */ }
        return reload('refresh-at-start');
      }
      claimLoopReload();
    };

    if (Date.now() >= fireAtLocal()) return ajax ? claimLoopAjax(false) : claimLoopReload();
    show('等待中', '', '準備中…');
    if (settings.timeSync && run.offset === undefined && run.target - Date.now() > 2500) await syncClock();
    if (stopped) return;
    // Show every coupon on one page while waiting (the page defaults to 6).
    if (ajax && run.target - Date.now() > 2000) requestList(C.couponViews(settings)[0], 1);
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
        else run = { ...nv, offset: run.offset ?? nv.offset, offsetError: run.offsetError ?? nv.offsetError };
      }
    });
    begin();
  }

  init();
})();
