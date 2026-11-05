// Network calls to Tigerair, made from the extension page. host_permissions
// in manifest.json let these cross-origin requests through without CORS.
import { collectStations, parseDailyPrices } from './core.js';

export const CMS = 'https://api-cms.tigerairtw.com';
export const BOOK = 'https://api-book.tigerairtw.com';

const HEADER_RULE_IDS = [1, 2];
const CACHE_TTL_MS = 10 * 60 * 1000;
const cache = new Map(); // url -> {at, promise}

/**
 * Make the API requests this extension sends look like the ones Tigerair's own
 * pages send (Origin/Referer of its site instead of chrome-extension://).
 * Scoped with initiatorDomains to this extension, so the user's normal
 * browsing of tigerairtw.com is untouched. Session rules vanish when Chrome
 * restarts, so the page installs them each time it opens.
 */
export async function installHeaderRules() {
  const rule = (id, domain, site) => ({
    id,
    priority: 1,
    action: {
      type: 'modifyHeaders',
      requestHeaders: [
        { header: 'origin', operation: 'set', value: site },
        { header: 'referer', operation: 'set', value: `${site}/` },
      ],
    },
    condition: {
      requestDomains: [domain],
      initiatorDomains: [chrome.runtime.id],
      resourceTypes: ['xmlhttprequest'],
    },
  });
  await chrome.declarativeNetRequest.updateSessionRules({
    removeRuleIds: HEADER_RULE_IDS,
    addRules: [
      rule(HEADER_RULE_IDS[0], 'api-cms.tigerairtw.com', 'https://www.tigerairtw.com'),
      rule(HEADER_RULE_IDS[1], 'api-book.tigerairtw.com', 'https://booking.tigerairtw.com'),
    ],
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function getJson(url, { timeoutMs = 20000, retries = 1 } = {}) {
  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const resp = await fetch(url, {
        signal: ctrl.signal,
        cache: 'no-store',
        credentials: 'omit',
        headers: { Accept: 'application/json', 'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8' },
      });
      if (resp.status === 429) throw new Error('查詢太頻繁，被虎航暫時限制（HTTP 429），請稍後再試');
      if (!resp.ok) throw new Error(`虎航回應 HTTP ${resp.status}`);
      const text = await resp.text();
      try {
        return JSON.parse(text);
      } catch {
        // A waiting room / bot check page comes back as HTML.
        throw new Error(/<html/i.test(text) ? '虎航回傳網頁而不是資料（可能是排隊頁或驗證頁）' : '虎航回應不是 JSON');
      }
    } catch (e) {
      lastErr = ctrl.signal.aborted ? new Error(`連線逾時（${timeoutMs / 1000} 秒）`) : e;
      if (e instanceof TypeError && !ctrl.signal.aborted) lastErr = new Error('連不到虎航（網路錯誤）');
      if (attempt < retries) await sleep(800 * (attempt + 1));
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastErr;
}

function cached(url, load) {
  const hit = cache.get(url);
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.promise;
  const promise = load();
  cache.set(url, { at: Date.now(), promise });
  promise.catch(() => cache.delete(url)); // never cache a failure
  return promise;
}

export function clearCache() {
  cache.clear();
}

/** Lowest one-way fare per day: Promise<{prices: Map<date, amount>, currency}>. */
export function dailyPrices(origin, destination, since, until) {
  const url = `${CMS}/api/app/book/daily-prices?${new URLSearchParams({
    origin, destination, userCurrency: 'TWD', pricingCurrency: 'TWD', since, until,
  })}`;
  return cached(url, () => getJson(url).then(parseDailyPrices));
}

/** Destinations Tigerair flies to from `origin`: Promise<Map<code, name>>. */
export function availableDestinations(origin) {
  const url = `${BOOK}/api/general/available-destinations?${new URLSearchParams({ locale: 'zh-TW', origin })}`;
  return cached(url, () => getJson(url, { timeoutMs: 10000, retries: 0 }).then(collectStations));
}
