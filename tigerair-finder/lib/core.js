// Pure logic shared by the search page and the tests: dates, validating the
// search form, planning which daily-price requests to make, parsing Tigerair's
// JSON, and combining outbound/return fares into trips. No Chrome APIs, no
// network, so `node --test` can exercise all of it.

// ------------------------------------------------------------------ dates ----
// Dates are 'YYYY-MM-DD' strings throughout. Arithmetic goes through a UTC day
// number so it never trips over time zones; ISO strings also compare correctly
// with < and >.
const DAY_MS = 86400000;
const ISO_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

export const WEEKDAY_ZH = ['日', '一', '二', '三', '四', '五', '六'];

export function isIsoDate(s) {
  const m = ISO_RE.exec(String(s || ''));
  if (!m) return false;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  return d.getUTCFullYear() === +m[1] && d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[3];
}

function toDay(iso) {
  const m = ISO_RE.exec(iso);
  return Date.UTC(+m[1], +m[2] - 1, +m[3]) / DAY_MS;
}

function fromDay(n) {
  return new Date(n * DAY_MS).toISOString().slice(0, 10);
}

export function addDays(iso, n) {
  return fromDay(toDay(iso) + n);
}

/** Whole days from a to b (b - a). */
export function daysBetween(a, b) {
  return toDay(b) - toDay(a);
}

/** 0 = Sunday … 6 = Saturday. */
export function weekday(iso) {
  return new Date(toDay(iso) * DAY_MS).getUTCDay();
}

/** The user's local calendar date, which is what "today" means on a booking site. */
export function localToday(now = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
}

/** '2026-12-05' -> '12/05（五）' */
export function shortDate(iso) {
  return `${iso.slice(5, 7)}/${iso.slice(8, 10)}（${WEEKDAY_ZH[weekday(iso)]}）`;
}

/** Split [since, until] at calendar-month boundaries. */
export function monthChunks(since, until) {
  const out = [];
  let from = since;
  while (from <= until) {
    const [y, m] = from.split('-').map(Number);
    const monthEnd = fromDay(Date.UTC(y, m, 1) / DAY_MS - 1); // day before the 1st of next month
    const to = monthEnd < until ? monthEnd : until;
    out.push({ since: from, until: to });
    from = addDays(to, 1);
  }
  return out;
}

// ------------------------------------------------------------------ query ----
export const LIMITS = Object.freeze({
  maxSpanDays: 366, // Tigerair sells roughly a year ahead
  days: [1, 60],
  maxDestinations: 40,
});

const CODE_RE = /^[A-Z0-9]{3}$/;

export function cleanCode(s) {
  return String(s || '').trim().toUpperCase();
}

/** Split free text like "nrt, KIX  oka" into valid station codes. */
export function parseCodes(text) {
  return String(text || '')
    .split(/[\s,，、;/]+/)
    .map(cleanCode)
    .filter((c) => CODE_RE.test(c));
}

function optNumber(v) {
  if (v === null || v === undefined || String(v).trim() === '') return null;
  const n = Number(String(v).replace(/[,\s]/g, ''));
  return Number.isFinite(n) ? n : NaN;
}

/**
 * Validate the raw form values. Returns {query, errors}; `query` is only
 * meaningful when `errors` is empty.
 *
 * Days are counted inclusively, the way Taiwanese travel ads do: leaving on
 * the 1st and returning on the 5th is a 5-day (5天4夜) trip.
 * For a round trip, [start, end] bounds the whole trip (earliest departure,
 * latest return); for one-way it bounds the departure date.
 */
export function normalizeQuery(raw, today) {
  const errors = [];
  const tripType = raw.tripType === 'oneWay' ? 'oneWay' : 'roundTrip';
  const origin = cleanCode(raw.origin);
  if (!CODE_RE.test(origin)) errors.push('請選擇出發地');

  const destinations = [...new Set((raw.destinations || []).map(cleanCode))]
    .filter((c) => CODE_RE.test(c) && c !== origin);
  if (!destinations.length) errors.push('請至少選一個目的地');
  if (destinations.length > LIMITS.maxDestinations) {
    errors.push(`目的地一次最多 ${LIMITS.maxDestinations} 個`);
  }

  let start = raw.start;
  const end = raw.end;
  if (!isIsoDate(start) || !isIsoDate(end)) {
    errors.push('請填寫可旅遊期間的開始與結束日期');
  } else {
    if (start < today) start = today; // can't fly in the past
    if (end < start) errors.push('可旅遊期間的結束日期不能早於開始日期（或今天）');
    else if (daysBetween(start, end) + 1 > LIMITS.maxSpanDays) {
      errors.push(`可旅遊期間最長 ${LIMITS.maxSpanDays} 天`);
    }
  }

  let minDays = null;
  let maxDays = null;
  if (tripType === 'roundTrip') {
    minDays = Math.round(Number(raw.minDays));
    maxDays = raw.maxDays === '' || raw.maxDays == null ? minDays : Math.round(Number(raw.maxDays));
    const [lo, hi] = LIMITS.days;
    if (!Number.isFinite(minDays) || !Number.isFinite(maxDays) || minDays < lo || maxDays > hi) {
      errors.push(`旅遊天數請填 ${lo}～${hi} 天`);
    } else if (maxDays < minDays) {
      errors.push('旅遊天數的上限不能小於下限');
    } else if (isIsoDate(start) && isIsoDate(end) && end >= start && daysBetween(start, end) + 1 < minDays) {
      errors.push(`可旅遊期間只有 ${daysBetween(start, end) + 1} 天，比最少旅遊天數 ${minDays} 天還短`);
    }
  }

  const minPrice = optNumber(raw.minPrice);
  const maxPrice = optNumber(raw.maxPrice);
  if (Number.isNaN(minPrice) || Number.isNaN(maxPrice) || minPrice < 0 || maxPrice < 0) {
    errors.push('價錢請填數字（可留空表示不限）');
  } else if (minPrice !== null && maxPrice !== null && maxPrice < minPrice) {
    errors.push('價錢上限不能小於下限');
  }

  const weekdays = [...new Set((raw.weekdays || []).map(Number))]
    .filter((d) => Number.isInteger(d) && d >= 0 && d <= 6)
    .sort();
  if (!weekdays.length) errors.push('出發星期至少要選一天');

  return {
    errors,
    query: { tripType, origin, destinations, start, end, minDays, maxDays, minPrice, maxPrice, weekdays },
  };
}

// --------------------------------------------------------------- planning ----
/**
 * The date range each direction has to cover. A round trip of at least
 * `minDays` can't leave later than end-(minDays-1) or return earlier than
 * start+(minDays-1), so those days are never requested.
 */
export function planLegs(q) {
  const legs = [];
  for (const dest of q.destinations) {
    if (q.tripType === 'oneWay') {
      legs.push({ dest, leg: 'out', origin: q.origin, destination: dest, since: q.start, until: q.end });
    } else {
      const span = q.minDays - 1;
      legs.push({ dest, leg: 'out', origin: q.origin, destination: dest, since: q.start, until: addDays(q.end, -span) });
      legs.push({ dest, leg: 'back', origin: dest, destination: q.origin, since: addDays(q.start, span), until: q.end });
    }
  }
  return legs;
}

/** planLegs, split into one request per calendar month. */
export function planRequests(q) {
  return planLegs(q).flatMap((l) => monthChunks(l.since, l.until).map((c) => ({ ...l, ...c })));
}

// ---------------------------------------------------------------- parsing ----
/**
 * Parse the daily-price JSON into {prices: Map<date, amount>, currency}.
 *
 * Known shapes:
 *   REST    api-cms.tigerairtw.com/api/app/book/daily-prices
 *           {"data": [{"date": "2026-12-01", "amount": 1799, ...}, ...]}
 *   GraphQL appLiveDailyPrices on api-book.tigerairtw.com/graphql
 *           {"data": {"appLiveDailyPrices": [{"date", "currency", "amount", ...}]}}
 *
 * An amount of 0 (or missing) means no flight or sold out that day. When a
 * date appears twice the lower amount wins.
 */
export function parseDailyPrices(json) {
  const list = Array.isArray(json) ? json
    : Array.isArray(json && json.data) ? json.data
      : Array.isArray(json && json.data && json.data.appLiveDailyPrices) ? json.data.appLiveDailyPrices
        : null;
  if (!list) {
    const why = json && (json.message || (Array.isArray(json.errors) && json.errors[0] && json.errors[0].message));
    throw new Error(why ? `虎航回應錯誤：${String(why).slice(0, 120)}` : '虎航回應的格式和預期不同（找不到每日價格）');
  }
  const prices = new Map();
  let currency = '';
  for (const it of list) {
    if (!it || typeof it !== 'object') continue;
    const date = typeof it.date === 'string' ? it.date.slice(0, 10) : '';
    if (!isIsoDate(date)) continue;
    const amount = Number(it.amount);
    if (!Number.isFinite(amount) || amount <= 0) continue;
    if (!prices.has(date) || amount < prices.get(date)) prices.set(date, amount);
    if (!currency && typeof it.currency === 'string') currency = it.currency;
  }
  return { prices, currency };
}

/**
 * Pull every station code (and a display name when there is one) out of the
 * route-list JSON from api-book.tigerairtw.com/api/general/*. The nesting has
 * changed over time, so walk the whole tree for objects with a `stationCode`.
 */
export function collectStations(json) {
  const out = new Map();
  const seen = new Set();
  const walk = (v) => {
    if (!v || typeof v !== 'object' || seen.has(v)) return;
    seen.add(v);
    if (Array.isArray(v)) { v.forEach(walk); return; }
    if (typeof v.stationCode === 'string' && CODE_RE.test(v.stationCode.trim().toUpperCase())) {
      const code = v.stationCode.trim().toUpperCase();
      const name = [v.stationName, v.name, v.displayName, v.cityName]
        .find((n) => typeof n === 'string' && n.trim());
      if (!out.has(code) || (name && !out.get(code))) out.set(code, name ? name.trim() : '');
    }
    Object.values(v).forEach(walk);
  };
  walk(json);
  return out;
}

/**
 * Merge request results into {dest: {out: Map, back: Map}}. Dates outside the
 * range that was asked for are dropped (the API has been seen returning extra
 * days around the requested window).
 */
export function mergeFares(requests, results) {
  const fares = {};
  requests.forEach((r, i) => {
    const res = results[i];
    if (!res || !res.ok) return;
    const f = (fares[r.dest] ||= { out: new Map(), back: new Map() });
    const target = f[r.leg];
    for (const [date, amount] of res.value.prices) {
      if (date < r.since || date > r.until) continue;
      if (!target.has(date) || amount < target.get(date)) target.set(date, amount);
    }
  });
  return fares;
}

// ------------------------------------------------------------------ trips ----
function inPriceRange(q, total) {
  return (q.minPrice === null || total >= q.minPrice) && (q.maxPrice === null || total <= q.maxPrice);
}

/**
 * Every trip that satisfies the query, cheapest first.
 * Trip: {dest, depart, ret, days, outPrice, backPrice, total}
 * (ret/days/backPrice are null for one-way).
 */
export function findTrips(q, fares) {
  const trips = [];
  for (const dest of q.destinations) {
    const f = fares[dest];
    if (!f) continue;
    for (const [depart, outPrice] of f.out) {
      if (depart < q.start || depart > q.end) continue;
      if (!q.weekdays.includes(weekday(depart))) continue;
      if (q.tripType === 'oneWay') {
        if (inPriceRange(q, outPrice)) {
          trips.push({ dest, depart, ret: null, days: null, outPrice, backPrice: null, total: outPrice });
        }
        continue;
      }
      for (let days = q.minDays; days <= q.maxDays; days++) {
        const ret = addDays(depart, days - 1);
        if (ret > q.end) break;
        const backPrice = f.back.get(ret);
        if (!backPrice) continue;
        const total = outPrice + backPrice;
        if (inPriceRange(q, total)) trips.push({ dest, depart, ret, days, outPrice, backPrice, total });
      }
    }
  }
  return sortTrips(trips, 'price');
}

export function sortTrips(trips, by = 'price') {
  const cmp = by === 'date'
    ? (a, b) => a.depart.localeCompare(b.depart) || a.total - b.total || (a.days || 0) - (b.days || 0) || a.dest.localeCompare(b.dest)
    : (a, b) => a.total - b.total || a.depart.localeCompare(b.depart) || (a.days || 0) - (b.days || 0) || a.dest.localeCompare(b.dest);
  return [...trips].sort(cmp);
}

/** Cheapest trip for each destination, cheapest destination first. */
export function cheapestPerDestination(trips) {
  const best = new Map();
  for (const t of sortTrips(trips, 'price')) if (!best.has(t.dest)) best.set(t.dest, t);
  return [...best.values()];
}

// ------------------------------------------------------------------ links ----
/** Deep link into booking.tigerairtw.com with the trip prefilled. */
export function bookingUrl(origin, trip, currency = 'TWD') {
  const p = new URLSearchParams({
    type: trip.ret ? 'roundTrip' : 'oneWay',
    outbound: `${origin}-${trip.dest}`,
  });
  if (trip.ret) p.set('inbound', `${trip.dest}-${origin}`);
  p.set('departureDate', trip.depart);
  if (trip.ret) p.set('returnDate', trip.ret);
  p.set('adult', '1');
  p.set('children', '0');
  p.set('infant', '0');
  p.set('languageCode', 'zh-tw');
  p.set('currencyCode', currency);
  return `https://booking.tigerairtw.com/?${p}`;
}

export function formatPrice(n) {
  return Math.round(n).toLocaleString('en-US');
}

export function tripLength(days) {
  return days === 1 ? '當天來回' : `${days}天${days - 1}夜`;
}

// ------------------------------------------------------------------- pool ----
/**
 * Run async task factories with at most `limit` in flight. Never rejects:
 * resolves to [{ok: true, value} | {ok: false, error}] in task order.
 */
export async function runPool(tasks, limit, onProgress) {
  const results = new Array(tasks.length);
  let next = 0;
  let done = 0;
  async function worker() {
    while (next < tasks.length) {
      const i = next++;
      try {
        results[i] = { ok: true, value: await tasks[i]() };
      } catch (error) {
        results[i] = { ok: false, error };
      }
      done++;
      if (onProgress) onProgress(done, tasks.length);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, tasks.length) }, worker));
  return results;
}
