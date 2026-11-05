import { legKey, parseFlightSearch } from './lib/core.js';

// The toolbar button opens the search page in a full tab (a popup is too
// small for the form and the results table).
const PAGE = chrome.runtime.getURL('search.html');
const MARK = 'tigerair-finder'; // see content/hook.js

function openSearch() {
  return chrome.tabs.create({ url: PAGE });
}

chrome.action.onClicked.addListener(openSearch);

chrome.runtime.onInstalled.addListener(({ reason }) => {
  if (reason === 'install') openSearch();
});

// ------------------------------------------------------- flight checking ----
// The daily low-fare calendar has no flight times. To filter by departure
// time, the search page asks for a booking search in a background tab: the
// booking site runs its own search (including its bot check) as for any
// visitor, content/hook.js captures the flight list it loads, and the tab is
// closed again. One tab at a time.

const sessions = new Map(); // tabId -> {onText}
let queue = Promise.resolve();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const LIMITS = {
  timeoutMs: 45000, // no answer at all
  queueMs: 180000, // the site's waiting room can take a while
  graceMs: 2500, // after a first answer, wait this long for the rest
};

async function checkFlights(url, need) {
  const want = need.map(legKey);
  const found = new Map();
  let firstAt = 0;
  const tab = await chrome.tabs.create({ url: `${url}#${MARK}`, active: false });
  sessions.set(tab.id, {
    onText(text) {
      try {
        for (const leg of parseFlightSearch(JSON.parse(text))) found.set(legKey(leg), leg);
        if (!firstAt) firstAt = Date.now();
      } catch {
        // some other GraphQL answer
      }
    },
  });
  try {
    const started = Date.now();
    let waitingRoom = false;
    for (;;) {
      await sleep(300);
      if (want.every((k) => found.has(k))) break;
      if (firstAt && Date.now() - firstAt > LIMITS.graceMs) break;
      const t = await chrome.tabs.get(tab.id).catch(() => null);
      if (!t) throw new Error('確認用的分頁被關掉了');
      if (/waiting-room/i.test(t.url || '')) waitingRoom = true;
      if (Date.now() - started > (waitingRoom ? LIMITS.queueMs : LIMITS.timeoutMs)) {
        throw new Error(waitingRoom ? '虎航訂票頁一直在排隊，請稍後再試' : '虎航訂票頁沒有回傳班機資料（逾時）');
      }
    }
    return [...found.values()];
  } finally {
    sessions.delete(tab.id);
    chrome.tabs.remove(tab.id).catch(() => {});
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || typeof msg !== 'object') return undefined;

  if (msg.type === 'flightSearchResult') {
    const s = sender.tab && sessions.get(sender.tab.id);
    if (s && typeof msg.text === 'string') s.onText(msg.text);
    return undefined;
  }

  if (msg.type === 'checkFlights' && String(sender.url || '').startsWith(PAGE)) {
    const url = String(msg.url || '');
    if (!url.startsWith('https://booking.tigerairtw.com/') || !Array.isArray(msg.need)) {
      sendResponse({ ok: false, error: 'bad request' });
      return undefined;
    }
    const run = queue.then(() => checkFlights(url, msg.need));
    queue = run.catch(() => {});
    run.then(
      (legs) => sendResponse({ ok: true, legs }),
      (e) => sendResponse({ ok: false, error: (e && e.message) || String(e) }),
    );
    return true; // async response
  }
  return undefined;
});
