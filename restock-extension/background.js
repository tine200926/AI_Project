import {
  classify, noOptionsReason, normalizeUrl, parseSale, regionCookie,
  resolveWatched, shopCountry,
} from './lib/core.js';
import {
  getSettings, getState, getTargets, log, saveState,
} from './lib/store.js';

const ALARM = 'check';
const COOKIE_RULE_ID = 1;
// Requests made by the service worker carry no tab; the cookie override is
// scoped to those so the user's own Weverse browsing is never touched.
const TAB_ID_NONE = -1;

// ------------------------------------------------------------- schedule ----
async function schedule() {
  const { intervalMinutes } = await getSettings();
  const existing = await chrome.alarms.get(ALARM);
  if (existing && existing.periodInMinutes === intervalMinutes) return;
  await chrome.alarms.create(ALARM, { delayInMinutes: 1, periodInMinutes: intervalMinutes });
}

chrome.runtime.onInstalled.addListener(async ({ reason }) => {
  await schedule();
  if (reason === 'install') chrome.runtime.openOptionsPage();
});
chrome.runtime.onStartup.addListener(schedule);

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.settings) {
    // Interval changed: recreate so the new period applies right away.
    chrome.alarms.clear(ALARM).then(schedule);
  }
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM) runCheck();
});

// ---------------------------------------------------------------- fetch ----
async function setCookieRule(url, cookie) {
  const addRules = cookie ? [{
    id: COOKIE_RULE_ID,
    priority: 1,
    action: {
      type: 'modifyHeaders',
      requestHeaders: [{ header: 'cookie', operation: 'set', value: cookie }],
    },
    condition: {
      urlFilter: `|${url}`,
      tabIds: [TAB_ID_NONE],
      resourceTypes: ['xmlhttprequest'],
    },
  }] : [];
  await chrome.declarativeNetRequest.updateSessionRules({ removeRuleIds: [COOKIE_RULE_ID], addRules });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function fetchPage(url, country, settings) {
  await setCookieRule(url, regionCookie(url, country));
  let lastErr;
  for (let attempt = 1; attempt <= settings.maxRetries; attempt++) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), settings.requestTimeoutSec * 1000);
    try {
      const resp = await fetch(url, {
        cache: 'no-store',
        signal: ctrl.signal,
        headers: { Accept: 'text/html,application/xhtml+xml', 'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8' },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return await resp.text();
    } catch (e) {
      lastErr = ctrl.signal.aborted ? new Error(`逾時（${settings.requestTimeoutSec} 秒）`) : e;
      if (attempt < settings.maxRetries) {
        log(`抓取第 ${attempt} 次失敗（${lastErr.message}），稍後重試：${url}`, 'WARN');
        await sleep(3000 * attempt);
      }
    } finally {
      clearTimeout(timer);
    }
  }
  throw new Error(`抓取失敗：${lastErr && lastErr.message}`);
}

/** Fetch + parse one sale page; throws a user-readable error. */
async function readSale(url, country, settings) {
  const html = await fetchPage(url, country, settings);
  const parsed = parseSale(html);
  if (!parsed.options.size) throw new Error(noOptionsReason(parsed, shopCountry(url, country)));
  return parsed;
}

// ---------------------------------------------------------------- check ----
let running = null;

function runCheck() {
  if (!running) {
    running = doCheck()
      .catch((e) => log(`檢查失敗：${e && e.message}`, 'ERROR'))
      .finally(() => { running = null; });
  }
  return running;
}

async function doCheck() {
  const [settings, allTargets, state] = await Promise.all([getSettings(), getTargets(), getState()]);
  const targets = allTargets.filter((t) => t.enabled !== false);
  const now = Date.now();
  const nowIso = new Date(now).toISOString();
  const results = {};
  const restocked = []; // {target, names}
  const wentOut = [];
  const failed = [];
  let availableCount = 0;

  for (let i = 0; i < targets.length; i++) {
    const t = targets[i];
    if (i > 0 && settings.politeDelayMs > 0) await sleep(settings.politeDelayMs);

    let parsed;
    try {
      parsed = await readSale(t.url, t.country, settings);
    } catch (e) {
      log(`[${t.label}] ${e.message}`, 'ERROR');
      failed.push({ target: t, reason: e.message });
      results[t.id] = { ok: false, error: e.message, at: nowIso };
      continue;
    }
    if (!parsed.statusKnown) {
      log(`[${t.label}] 不認得的商品狀態「${parsed.statusCode}」，改用各選項的售完旗標判斷`, 'WARN');
    }

    const { resolved, missing } = resolveWatched(t.watch || ['*'], parsed.options.keys());
    if (missing.length) {
      log(`[${t.label}] 頁面上找不到選項：${missing.join(', ')}`, 'WARN');
    }
    const watchedSet = new Set(resolved);
    const hits = [];
    const outs = [];

    for (const name of resolved) {
      const soldOut = parsed.options.get(name);
      const key = `${t.id}::${name}`;
      const prev = state[key];
      const { kind } = classify(prev, soldOut, now, settings);
      if (!soldOut) availableCount++;

      if (kind === 'restock') {
        hits.push(name);
        state[key] = { soldOut: false, checkedAt: nowIso, notifiedAt: nowIso };
        log(`[${t.label}] 補貨：${name}`, 'HIT');
        continue;
      }
      if (kind === 'soldout') {
        log(`[${t.label}] 又賣完：${name}`);
        if (settings.notifyOnSoldOut) outs.push(name);
      } else if (kind === 'new') {
        log(`[${t.label}] 開始監測「${name}」：${soldOut ? '售完' : '有貨'}`);
      }
      state[key] = { soldOut, checkedAt: nowIso, notifiedAt: prev ? prev.notifiedAt || null : null };
    }

    if (hits.length) restocked.push({ target: t, names: hits });
    if (outs.length) wentOut.push({ target: t, names: outs });
    results[t.id] = {
      ok: true,
      at: nowIso,
      statusCode: parsed.statusCode,
      saleName: parsed.saleName,
      options: [...parsed.options].map(([n, s]) => [n, s, watchedSet.has(n)]),
      missing,
    };
  }

  for (const r of restocked) await notifyTarget('restock', r.target, r.names);
  for (const r of wentOut) await notifyTarget('soldout', r.target, r.names);

  // Tell the user if the monitor itself is broken, once per cooldown window,
  // and clear the flag once every page is readable again.
  if (failed.length && settings.notifyOnError) {
    const last = state['error::notifiedAt'] ? Date.parse(state['error::notifiedAt']) : NaN;
    if (Number.isNaN(last) || (now - last) / 60000 >= settings.cooldownMinutes) {
      await notifyError(failed);
      state['error::notifiedAt'] = nowIso;
    }
  } else if (!failed.length) {
    delete state['error::notifiedAt'];
  }

  await saveState(state);
  await chrome.storage.local.set({ lastRun: { at: nowIso, results } });
  await updateBadge(availableCount, failed.length);
  log(`檢查完成：${targets.length} 個商品，${availableCount} 個監測選項有貨` +
    (failed.length ? `，${failed.length} 個失敗` : ''));
}

// ---------------------------------------------------------- notifications ----
async function rememberNotification(id, url) {
  // The worker can be stopped between showing and clicking a notification.
  await chrome.storage.session.set({ [`n:${id}`]: url });
}

async function notifyTarget(kind, target, names) {
  const id = `${kind}:${target.id}:${Date.now()}`;
  const restock = kind === 'restock';
  await chrome.notifications.create(id, {
    type: 'basic',
    iconUrl: 'icons/icon128.png',
    title: `${restock ? '🎉 補貨' : '😢 又賣完了'}：${target.label}`,
    message: names.join('、'),
    contextMessage: restock ? '點一下開啟商品頁' : '',
    priority: restock ? 2 : 0,
    requireInteraction: restock,
  });
  await rememberNotification(id, target.url);
}

async function notifyError(failed) {
  const id = `error:${Date.now()}`;
  await chrome.notifications.create(id, {
    type: 'basic',
    iconUrl: 'icons/icon128.png',
    title: '⚠️ 補貨監測異常',
    message: failed.map((f) => `${f.target.label}：${f.reason}`).join('\n').slice(0, 500),
    priority: 1,
  });
  await rememberNotification(id, failed.length === 1 ? failed[0].target.url : '');
}

chrome.notifications.onClicked.addListener(async (id) => {
  const key = `n:${id}`;
  const stored = await chrome.storage.session.get(key);
  const url = stored[key];
  if (url) chrome.tabs.create({ url });
  else chrome.runtime.openOptionsPage();
  chrome.notifications.clear(id);
  chrome.storage.session.remove(key);
});

chrome.notifications.onClosed.addListener((id) => chrome.storage.session.remove(`n:${id}`));

async function updateBadge(available, failedCount) {
  if (failedCount) {
    await chrome.action.setBadgeBackgroundColor({ color: '#d93025' });
    await chrome.action.setBadgeText({ text: '!' });
    await chrome.action.setTitle({ title: `補貨通知：${failedCount} 個商品讀取失敗` });
  } else if (available) {
    await chrome.action.setBadgeBackgroundColor({ color: '#188038' });
    await chrome.action.setBadgeText({ text: String(available) });
    await chrome.action.setTitle({ title: `補貨通知：${available} 個監測選項有貨` });
  } else {
    await chrome.action.setBadgeText({ text: '' });
    await chrome.action.setTitle({ title: '補貨通知' });
  }
}

// -------------------------------------------------------------- messages ----
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    if (msg.type === 'checkNow') {
      await runCheck();
      return { ok: true };
    }
    if (msg.type === 'probe') {
      // Used by the options page to list a page's options before adding it.
      // Goes through the worker so the region-cookie rule applies.
      const settings = await getSettings();
      const url = normalizeUrl(msg.url);
      const parsed = await readSale(url, msg.country || '', settings);
      return {
        ok: true,
        url,
        saleName: parsed.saleName,
        statusCode: parsed.statusCode,
        options: [...parsed.options],
      };
    }
    if (msg.type === 'testNotify') {
      await chrome.notifications.create(`test:${Date.now()}`, {
        type: 'basic',
        iconUrl: 'icons/icon128.png',
        title: '🔔 測試通知',
        message: '看得到這則通知，補貨時就會收到提醒。',
      });
      return { ok: true };
    }
    return { ok: false, error: `unknown message ${msg.type}` };
  })()
    .then(sendResponse)
    .catch((e) => sendResponse({ ok: false, error: e && e.message ? e.message : String(e) }));
  return true; // keep the channel open for the async reply
});
