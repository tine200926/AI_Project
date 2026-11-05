// Service worker: keeps the alarms in sync with the schedules, opens the
// coupon page shortly before each target time and records the outcome.
// The precise timing and clicking happens in content.js.
importScripts('lib/core.js');
const C = self.CouponCore;

const LOG_MAX = 200;
const PAGE_MATCH = 'https://myship.7-11.com.tw/mem/GetCoupon*';

async function readState() {
  const d = await chrome.storage.local.get(['settings', 'schedules', 'lastFired', 'run']);
  return {
    settings: C.sanitizeSettings(d.settings),
    schedules: Array.isArray(d.schedules) ? d.schedules : [],
    lastFired: d.lastFired || {},
    run: d.run || null,
  };
}

// Serialize every read-modify-write of storage so messages arriving together
// (click counts, dialogs, finish) can't overwrite each other.
let chain = Promise.resolve();
function locked(fn) {
  const p = chain.then(fn, fn);
  chain = p.catch(() => {});
  return p;
}

async function addLog(msg, level = 'info') {
  const { log = [] } = await chrome.storage.local.get('log');
  log.unshift({ t: Date.now(), msg, level });
  await chrome.storage.local.set({ log: log.slice(0, LOG_MAX) });
}

async function setBadge(run) {
  if (run) {
    await chrome.action.setBadgeBackgroundColor({ color: '#e8710a' });
    await chrome.action.setBadgeText({ text: '搶' });
  } else {
    await chrome.action.setBadgeText({ text: '' });
  }
}

// ------------------------------------------------------------ schedules --
async function reschedule() {
  const { settings, schedules, lastFired, run } = await readState();
  for (const a of await chrome.alarms.getAll()) {
    if (a.name.startsWith('sched:')) await chrome.alarms.clear(a.name);
  }
  const now = Date.now();
  const upcoming = {};
  for (const s of schedules) {
    if (!s.enabled) continue;
    // A target that passed less than `durationSec` ago (Chrome was just
    // started, the computer woke up) still gets a run.
    const after = Math.max(now - settings.durationSec * 1000, lastFired[s.id] || 0);
    const target = C.nextRun(s, after);
    if (target === null) continue;
    upcoming[s.id] = target;
    const when = Math.max(target - settings.leadSec * 1000, now + 200);
    await chrome.alarms.create(`sched:${s.id}:${target}`, { when });
  }
  await chrome.storage.local.set({ upcoming });
  await setBadge(run && run.end > now ? run : null);
}

async function findCouponTab() {
  const tabs = await chrome.tabs.query({ url: PAGE_MATCH });
  return tabs.find((t) => t.active) || tabs[0] || null;
}

async function startRun(target, label, scheduleId = null) {
  const { settings, run: old } = await readState();
  const now = Date.now();
  if (old && old.end > now) {
    if (old.target === target) return old;
    await addLog(`「${old.label}」還在執行，改成執行「${label}」`, 'warn');
  }
  const run = {
    id: `${now.toString(36)}${Math.random().toString(36).slice(2, 7)}`,
    label,
    scheduleId,
    target,
    end: target + settings.durationSec * 1000,
    createdAt: now,
    tabId: null,
    clicks: 0,
    reloads: 0,
    messages: [],
  };

  // Tab in front: Chrome throttles timers in background tabs to 1/second.
  let tab = await findCouponTab();
  if (tab) {
    run.tabId = tab.id;
    await chrome.storage.local.set({ run });
    await chrome.tabs.update(tab.id, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true }).catch(() => {});
    await chrome.tabs.reload(tab.id);
  } else {
    await chrome.storage.local.set({ run });
    tab = await chrome.tabs.create({ url: C.PAGE_URL, active: true });
    run.tabId = tab.id;
    await chrome.storage.local.set({ run });
    await chrome.windows.update(tab.windowId, { focused: true }).catch(() => {});
  }
  await chrome.alarms.create(`runEnd:${run.id}`, { when: run.end + 8000 });
  await addLog(`開始「${label}」，目標時間 ${C.fmtDateTime(target, true)}`);
  await setBadge(run);
  return run;
}

async function finishRun(id, result, detail = '') {
  const { run } = await readState();
  if (!run || run.id !== id) return;
  await chrome.storage.local.set({ run: null });
  await chrome.alarms.clear(`runEnd:${id}`);
  await setBadge(null);

  const msgs = (run.messages || []).slice(-3).join(' / ');
  const titles = {
    done: '優惠券領取完成',
    timeout: '優惠券時間到，已停止',
    login: '需要先登入賣貨便',
    stopped: '已停止領取',
    nobutton: '沒有找到領取按鈕',
  };
  const title = titles[result] || '優惠券領取結束';
  const body = [`「${run.label}」點擊 ${run.clicks} 次`, detail, msgs && `網站訊息：${msgs}`]
    .filter(Boolean).join('\n');
  await addLog(`${title}｜${body.replace(/\n/g, '｜')}`, result === 'done' ? 'ok' : (result === 'stopped' ? 'info' : 'warn'));
  if (result !== 'stopped') {
    chrome.notifications.create(`run:${id}`, {
      type: 'basic',
      iconUrl: 'icons/icon128.png',
      title,
      message: body.slice(0, 250),
      priority: 2,
    });
  }
}

// --------------------------------------------------------------- events --
chrome.runtime.onInstalled.addListener(async (details) => {
  const d = await chrome.storage.local.get(['settings', 'schedules']);
  await chrome.storage.local.set({
    settings: C.sanitizeSettings(d.settings),
    schedules: Array.isArray(d.schedules) ? d.schedules : [],
  });
  await reschedule();
  if (details.reason === 'install') chrome.runtime.openOptionsPage();
});

chrome.runtime.onStartup.addListener(() => locked(reschedule));

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.schedules || changes.settings) locked(reschedule);
});

chrome.alarms.onAlarm.addListener((alarm) => locked(async () => {
  const [kind, a, b] = alarm.name.split(':');
  if (kind === 'sched') {
    const target = Number(b);
    const { schedules, lastFired } = await readState();
    const s = schedules.find((x) => x.id === a);
    if (!s || !s.enabled) return;
    lastFired[a] = target;
    await chrome.storage.local.set({ lastFired });
    await startRun(target, s.name || C.describeSchedule(s), s.id);
    await reschedule();
  } else if (kind === 'runEnd') {
    const { run } = await readState();
    if (run && run.id === a) {
      await finishRun(a, run.clicks ? 'timeout' : 'nobutton',
        run.clicks ? '' : '可能是還沒登入、分頁被關掉，或按鈕文字跟設定的關鍵字不同');
    }
  }
}));

chrome.tabs.onRemoved.addListener((tabId) => locked(async () => {
  const { run } = await readState();
  if (run && run.tabId === tabId) await finishRun(run.id, 'stopped', '優惠券分頁被關閉');
}));

chrome.notifications.onClicked.addListener(async (id) => {
  chrome.notifications.clear(id);
  const tab = await findCouponTab();
  if (tab) {
    await chrome.tabs.update(tab.id, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true }).catch(() => {});
  } else {
    await chrome.tabs.create({ url: C.PAGE_URL });
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const reply = (p) => p.then((v) => sendResponse(v ?? { ok: true }), (e) => sendResponse({ ok: false, error: String(e) }));
  switch (msg && msg.type) {
    case 'whoami':
      sendResponse({ tabId: sender.tab ? sender.tab.id : null });
      return false;
    case 'startNow':
      reply(locked(() => startRun(Date.now() + (Number(msg.delayMs) || 5000), '手動執行')));
      return true;
    case 'stop':
      reply(locked(async () => {
        const { run } = await readState();
        if (run) await finishRun(run.id, 'stopped', '手動停止');
      }));
      return true;
    case 'runPatch':
      reply(locked(async () => {
        const { run } = await readState();
        if (!run || run.id !== msg.id) return { ok: false };
        const p = msg.patch || {};
        if (p.addClicks) run.clicks += p.addClicks;
        if (p.addReloads) run.reloads += p.addReloads;
        if (p.message) run.messages = [...(run.messages || []), String(p.message).slice(0, 120)].slice(-10);
        if (p.offset !== undefined) run.offset = p.offset;
        if (p.offsetError !== undefined) run.offsetError = p.offsetError;
        if (p.status) run.status = p.status;
        await chrome.storage.local.set({ run });
        if (p.message) await addLog(`網站訊息：${p.message}`);
        return { ok: true, run };
      }));
      return true;
    case 'runFinish':
      reply(locked(() => finishRun(msg.id, msg.result, msg.detail || '')));
      return true;
    case 'log':
      reply(locked(() => addLog(String(msg.msg || ''), msg.level || 'info')));
      return true;
    default:
      return false;
  }
});
