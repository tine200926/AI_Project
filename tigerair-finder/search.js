import {
  addDays, bookingUrl, cheapestPerDestination, findTrips, formatPrice, hasTimeFilter, legUrl,
  localToday, mergeFares, normalizeQuery, parseCodes, planRequests, runPool, shortDate, sortTrips,
  tripLength, verifyTrips, WEEKDAY_ZH,
} from './lib/core.js';
import { COUNTRIES, STATIONS, stationInfo, stationName } from './lib/stations.js';
import { availableDestinations, dailyPrices, installHeaderRules } from './lib/api.js';

const CONCURRENCY = 3;
const PAGE_SIZE = 50;
const VERIFY_KEEP = 5; // confirm this many of the cheapest time-matching trips
const VERIFY_BUDGET = 10; // booking-page searches per round of checking
const WEEK_ORDER = [1, 2, 3, 4, 5, 6, 0]; // Monday first

const $ = (id) => document.getElementById(id);

/** Tiny DOM builder; text always goes in via textContent. */
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v === null || v === undefined) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const ui = {
  selected: new Set(),
  served: null, // Map<code, name> from the live route list, or null when unknown
  routeStatus: 'idle', // idle | loading | ok | failed
  trips: [],
  query: null,
  shown: PAGE_SIZE,
  allFailed: false,
  searchId: 0,
  verify: null, // departure-time check, see startVerify
};

const nameOf = (code) => stationName(code, ui.served);

// ------------------------------------------------------------------- form ----
function defaults() {
  const today = localToday();
  return {
    tripType: 'roundTrip',
    origin: 'TPE',
    destinations: [],
    extraCodes: '',
    start: addDays(today, 1),
    end: addDays(today, 60),
    minDays: 3,
    maxDays: 5,
    minPrice: '',
    maxPrice: '',
    weekdays: [0, 1, 2, 3, 4, 5, 6],
    outTimeFrom: '',
    outTimeTo: '',
    backTimeFrom: '',
    backTimeTo: '',
    sortBy: 'price',
  };
}

const TIME_FIELDS = ['outTimeFrom', 'outTimeTo', 'backTimeFrom', 'backTimeTo'];

function tripType() {
  return document.querySelector('input[name="tripType"]:checked').value;
}

function readForm() {
  return {
    tripType: tripType(),
    origin: $('origin').value,
    destinations: [...ui.selected].filter(isVisibleDest).concat(parseCodes($('extraCodes').value)),
    extraCodes: $('extraCodes').value,
    start: $('start').value,
    end: $('end').value,
    minDays: $('minDays').value,
    maxDays: $('maxDays').value,
    minPrice: $('minPrice').value,
    maxPrice: $('maxPrice').value,
    weekdays: [...document.querySelectorAll('#weekdays input:checked')].map((i) => Number(i.value)),
    ...Object.fromEntries(TIME_FIELDS.map((k) => [k, $(k).value])),
    sortBy: $('sortBy').value,
  };
}

function writeForm(f) {
  document.querySelector(`input[name="tripType"][value="${f.tripType === 'oneWay' ? 'oneWay' : 'roundTrip'}"]`).checked = true;
  $('origin').value = f.origin;
  if (!$('origin').value) $('origin').value = 'TPE';
  ui.selected = new Set(f.destinations || []);
  $('extraCodes').value = f.extraCodes || '';
  $('start').value = f.start;
  $('end').value = f.end;
  $('minDays').value = f.minDays;
  $('maxDays').value = f.maxDays;
  $('minPrice').value = f.minPrice;
  $('maxPrice').value = f.maxPrice;
  for (const i of document.querySelectorAll('#weekdays input')) i.checked = f.weekdays.includes(Number(i.value));
  for (const k of TIME_FIELDS) $(k).value = typeof f[k] === 'string' ? f[k] : '';
  $('sortBy').value = f.sortBy === 'date' ? 'date' : 'price';
}

async function loadSavedForm() {
  const base = defaults();
  try {
    const { form } = await chrome.storage.local.get('form');
    if (!form || typeof form !== 'object') return base;
    const f = { ...base, ...form };
    // An old saved window that has already passed is useless; start fresh dates.
    if (!f.end || f.end < localToday()) { f.start = base.start; f.end = base.end; }
    if (!Array.isArray(f.weekdays) || !f.weekdays.length) f.weekdays = base.weekdays;
    if (!Array.isArray(f.destinations)) f.destinations = [];
    return f;
  } catch {
    return base;
  }
}

function saveForm() {
  const f = readForm();
  f.destinations = [...ui.selected]; // keep picks hidden by the current origin too
  chrome.storage.local.set({ form: f }).catch(() => {});
}

function syncTripType() {
  const round = tripType() === 'roundTrip';
  $('daysField').hidden = !round;
  $('backTimeRow').hidden = !round;
  $('periodHint').textContent = round
    ? '最早出發日 ～ 最晚回程日，整趟旅程都在這段期間內'
    : '在這段期間內出發';
  $('priceHint').textContent = round ? '來回是去程 + 回程合計；留空表示不限' : '留空表示不限';
}

// ------------------------------------------------------------ origin/dest ----
function renderOrigins() {
  const sel = $('origin');
  for (const c of COUNTRIES) {
    const list = STATIONS.filter((s) => s.country === c.code);
    if (!list.length) continue;
    sel.append(h('optgroup', { label: c.name },
      list.map((s) => h('option', { value: s.code, text: `${s.name}（${s.code}）` }))));
  }
}

function isVisibleDest(code) {
  if (code === $('origin').value) return false;
  return !ui.served || ui.served.has(code);
}

function renderDestinations() {
  const origin = $('origin').value;
  const box = $('destGroups');
  box.replaceChildren();

  const groups = new Map(COUNTRIES.map((c) => [c.code, []]));
  const codes = ui.served
    ? [...ui.served.keys()]
    : STATIONS.map((s) => s.code);
  for (const code of codes) {
    if (code === origin) continue;
    const info = stationInfo(code);
    groups.get(info ? info.country : 'OTHER').push(code);
  }

  for (const c of COUNTRIES) {
    const list = groups.get(c.code);
    if (!list.length) continue;
    const allOn = list.every((code) => ui.selected.has(code));
    box.append(h('div', { class: 'dest-group' },
      h('div', { class: 'dest-head' },
        h('span', { class: 'dest-country', text: c.name }),
        h('button', {
          type: 'button',
          class: 'link',
          text: allOn ? '取消全選' : '全選',
          onclick: () => {
            for (const code of list) allOn ? ui.selected.delete(code) : ui.selected.add(code);
            renderDestinations();
          },
        })),
      h('div', { class: 'chips' }, list.map((code) => h('label', { class: 'chip', title: code },
        h('input', {
          type: 'checkbox',
          value: code,
          checked: ui.selected.has(code),
          onchange: (e) => {
            e.target.checked ? ui.selected.add(code) : ui.selected.delete(code);
            renderDestinations();
          },
        }),
        h('span', {}, nameOf(code), h('small', { text: code })))))));
  }
  updateDestCount();
}

function updateDestCount() {
  const n = readForm().destinations.length;
  $('destCount').textContent = n ? `已選 ${n} 個` : '';
}

async function loadRoutes() {
  const origin = $('origin').value;
  ui.routeStatus = 'loading';
  ui.served = null;
  $('routeNote').textContent = '正在讀取虎航航線…';
  renderDestinations();
  let served = null;
  try {
    served = await availableDestinations(origin);
  } catch {
    served = null;
  }
  if ($('origin').value !== origin) return; // origin changed while loading
  if (served && served.size) {
    ui.served = served;
    ui.routeStatus = 'ok';
    $('routeNote').textContent = `只列出虎航從${nameOf(origin)}有飛的 ${served.size} 個航點。`;
  } else {
    ui.routeStatus = 'failed';
    $('routeNote').textContent = '讀不到虎航航線清單，先列出全部航點；沒有飛的航線查詢時會顯示「沒有航班」。';
  }
  renderDestinations();
}

function renderWeekdays() {
  $('weekdays').replaceChildren(...WEEK_ORDER.map((d) => h('label', { class: 'chip day' },
    h('input', { type: 'checkbox', value: d, checked: true }),
    h('span', { text: WEEKDAY_ZH[d] }))));
}

function setWeekdays(days) {
  for (const i of document.querySelectorAll('#weekdays input')) i.checked = days.includes(Number(i.value));
  saveForm();
}

// ----------------------------------------------------------------- search ----
function showErrors(list) {
  $('errors').replaceChildren(...list.map((e) => h('div', { text: e })));
}

function setStatus(text, ratio) {
  $('status').hidden = false;
  $('statusText').textContent = text;
  $('barFill').style.width = `${Math.round((ratio || 0) * 100)}%`;
}

async function search(ev) {
  if (ev) ev.preventDefault();
  const today = localToday();
  const { query, errors } = normalizeQuery(readForm(), today);
  showErrors(errors);
  if (errors.length) return;
  saveForm();

  const id = ++ui.searchId;
  ui.verify = null; // stops a time check still running for the previous search
  renderVerified();
  const requests = planRequests(query);
  $('go').disabled = true;
  $('results').hidden = true;
  setStatus(`查詢中… 0 / ${requests.length}`, 0);

  const results = await runPool(
    requests.map((r) => () => dailyPrices(r.origin, r.destination, r.since, r.until)),
    CONCURRENCY,
    (done, total) => { if (id === ui.searchId) setStatus(`查詢中… ${done} / ${total}`, done / total); },
  );
  if (id !== ui.searchId) return;

  const fares = mergeFares(requests, results);
  ui.query = query;
  ui.trips = findTrips(query, fares);
  ui.shown = PAGE_SIZE;
  $('go').disabled = false;
  $('status').hidden = true;
  ui.allFailed = renderWarnings(query, requests, results, fares);
  renderResults();
  if (hasTimeFilter(query)) startVerify(query, fares);
}

function renderWarnings(q, requests, results, fares) {
  const box = $('warnings');
  box.replaceChildren();
  const failed = new Map(); // dest -> Set(messages)
  requests.forEach((r, i) => {
    if (results[i].ok) return;
    const msg = (results[i].error && results[i].error.message) || '未知錯誤';
    if (!failed.has(r.dest)) failed.set(r.dest, new Set());
    failed.get(r.dest).add(msg);
  });

  if (results.every((r) => !r.ok)) {
    const msgs = [...new Set([...failed.values()].flatMap((s) => [...s]))];
    box.append(h('div', { class: 'alert error' },
      h('strong', { text: '查詢失敗：' }), msgs.join('；'),
      h('div', { class: 'hint', text: '請確認瀏覽器打得開 tigerairtw.com；如果一直失敗，可能是虎航更改了網站，或暫時限制了查詢次數。' })));
    return true;
  }
  for (const [dest, msgs] of failed) {
    const allOfDest = requests.every((r, i) => r.dest !== dest || !results[i].ok);
    const what = allOfDest ? '查詢失敗' : '部分日期查詢失敗';
    box.append(h('div', { class: 'alert warn', text: `${nameOf(dest)}：${what}（${[...msgs].join('；')}），${allOfDest ? '沒有列出這個目的地' : '結果可能不完整'}。` }));
  }
  const empty = q.destinations.filter((d) => !failed.has(d) && (!fares[d] || !fares[d].out.size));
  if (empty.length) {
    box.append(h('div', { class: 'alert info', text: `${empty.map(nameOf).join('、')}：這段期間沒有可訂的${q.tripType === 'roundTrip' ? '去程' : ''}航班（沒有飛或已售完）。` }));
  }
  const noBack = q.tripType === 'roundTrip'
    ? q.destinations.filter((d) => !failed.has(d) && fares[d] && fares[d].out.size && !fares[d].back.size)
    : [];
  if (noBack.length) {
    box.append(h('div', { class: 'alert info', text: `${noBack.map(nameOf).join('、')}：這段期間沒有可訂的回程航班。` }));
  }
  return false;
}

function renderResults() {
  const q = ui.query;
  const trips = sortTrips(ui.trips, $('sortBy').value);
  $('results').hidden = false;
  $('resultsNote').hidden = !(hasTimeFilter(q) && trips.length);

  $('sortWrap').hidden = !trips.length;
  if (!trips.length) {
    $('summary').textContent = ui.allFailed ? '查詢失敗' : '沒有符合條件的機票';
    $('best').replaceChildren(...(ui.allFailed ? [] : [h('p', { class: 'hint', text: '可以試試放寬價錢、拉長可旅遊期間、調整旅遊天數或出發星期。' })]));
    $('tableWrap').hidden = true;
    $('more').hidden = true;
    return;
  }

  const cheapest = sortTrips(trips, 'price')[0];
  $('summary').textContent = `找到 ${trips.length.toLocaleString('en-US')} 個符合的${q.tripType === 'roundTrip' ? '組合' : '日期'}，最便宜 TWD ${formatPrice(cheapest.total)}`;

  const perDest = cheapestPerDestination(trips);
  $('best').replaceChildren(...(perDest.length > 1 ? perDest : []).map((t) => h('a', {
    class: `best-card${t === cheapest ? ' top' : ''}`,
    href: bookingUrl(q.origin, t),
    target: '_blank',
    rel: 'noopener',
  },
  h('span', { class: 'best-dest', text: nameOf(t.dest) }),
  h('span', { class: 'best-price', text: `TWD ${formatPrice(t.total)}` }),
  h('span', { class: 'best-when', text: t.ret ? `${shortDate(t.depart)} → ${shortDate(t.ret)}` : shortDate(t.depart) }))));

  const round = q.tripType === 'roundTrip';
  $('tableWrap').hidden = false;
  $('table').tHead.replaceChildren(h('tr', {},
    round
      ? ['#', '目的地', '去程', '回程', '天數', '去程價', '回程價', '合計', '']
        .map((t, i) => h('th', { class: i >= 5 && i <= 7 ? 'num' : '', text: t }))
      : ['#', '目的地', '出發日', '票價', ''].map((t, i) => h('th', { class: i === 3 ? 'num' : '', text: t }))));

  const rows = trips.slice(0, ui.shown).map((t, i) => h('tr', { class: t.total === cheapest.total ? 'cheapest' : '' },
    h('td', { class: 'rank', text: i + 1 }),
    h('td', {}, nameOf(t.dest), h('small', { text: ` ${t.dest}` })),
    h('td', { text: shortDate(t.depart) }),
    round && h('td', { text: shortDate(t.ret) }),
    round && h('td', { text: tripLength(t.days) }),
    round && h('td', { class: 'num', text: formatPrice(t.outPrice) }),
    round && h('td', { class: 'num', text: formatPrice(t.backPrice) }),
    h('td', { class: 'num total', text: `TWD ${formatPrice(t.total)}` }),
    h('td', {}, h('a', { class: 'book', href: bookingUrl(q.origin, t), target: '_blank', rel: 'noopener', text: '去訂票' }))));
  $('table').tBodies[0].replaceChildren(...rows);

  const left = trips.length - ui.shown;
  $('more').hidden = left <= 0;
  $('more').textContent = `顯示更多（還有 ${left.toLocaleString('en-US')} 筆）`;
}

// ------------------------------------------------------------- time check ----
// The calendar has one price per day and no flight times, so departure-time
// filtering checks the cheapest candidates on the booking site itself (see
// background.js), cheapest first, until the best matches are certain.

function startVerify(query, fares) {
  // The price floor is applied to real prices only: a calendar price under
  // the floor can still turn into a real price above it.
  const candidates = findTrips({ ...query, minPrice: null }, fares);
  if (!candidates.length) return;
  ui.verify = {
    q: query,
    candidates,
    legCache: new Map(),
    verified: [],
    nextIndex: 0,
    keep: VERIFY_KEEP,
    searches: 0,
    roundSearches: 0,
    errors: [],
    running: false,
    cancelled: false,
    stopReason: '',
  };
  runVerify();
}

async function checkFlightsFor(q, missing, trip) {
  const url = missing.length === 2 ? bookingUrl(q.origin, trip) : legUrl(missing[0]);
  const r = await chrome.runtime.sendMessage({ type: 'checkFlights', url, need: missing });
  if (!r) throw new Error('擴充功能背景沒有回應');
  if (!r.ok) throw new Error(r.error);
  return r.legs;
}

async function runVerify() {
  const v = ui.verify;
  if (!v || v.running) return;
  v.running = true;
  v.cancelled = false;
  v.roundSearches = 0;
  v.roundErrors = [];
  renderVerified();
  const res = await verifyTrips({
    q: v.q,
    candidates: v.candidates,
    legCache: v.legCache,
    search: (missing, trip) => checkFlightsFor(v.q, missing, trip),
    keep: v.keep,
    maxSearches: VERIFY_BUDGET,
    startIndex: v.nextIndex,
    verified: v.verified,
    isCancelled: () => ui.verify !== v || v.cancelled,
    onUpdate: (st) => {
      if (ui.verify !== v) return;
      v.verified = st.verified;
      v.roundSearches = st.searches;
      v.roundErrors = st.errors;
      renderVerified();
    },
  });
  if (ui.verify !== v) return;
  Object.assign(v, {
    running: false,
    verified: res.verified,
    nextIndex: res.nextIndex,
    searches: v.searches + res.searches,
    errors: [...v.errors, ...res.errors],
    stopReason: res.stopReason,
  });
  renderVerified();
}

function flightLine(f) {
  return h('span', { class: 'flight', text: `${f.flightNo} ${f.dep} → ${f.arr}${f.stops ? `（轉機 ${f.stops} 次）` : ''}` });
}

function renderVerified() {
  const v = ui.verify;
  $('verified').hidden = !v;
  if (!v) return;
  const q = v.q;
  const round = q.tripType === 'roundTrip';
  const list = v.verified;
  const when = [
    q.outTime && `去程 ${q.outTime.from}～${q.outTime.to}`,
    q.backTime && `回程 ${q.backTime.from}～${q.backTime.to}`,
  ].filter(Boolean).join('、') + ' 出發';

  $('vSummary').textContent = list.length
    ? `${when}：最便宜 TWD ${formatPrice(list[0].total)}（含稅）`
    : v.running ? `正在確認 ${when} 的班機…` : `${when}：沒有找到符合的班機`;

  const done = v.searches + (v.running ? v.roundSearches : 0);
  const status = {
    bound: `已確認 ${done} 組日期。這幾筆就是符合時間的最便宜組合：其他日期光是每日最低價就已經比它們貴。`,
    exhausted: list.length
      ? `所有候選日期都確認完了（查了 ${done} 組）。`
      : `所有候選日期都確認完了（查了 ${done} 組），在指定時段都沒有可訂的班機，可以放寬出發時間或日期。`,
    budget: `已確認 ${done} 組日期，先停在這裡；可能還有更便宜的沒確認到，可以按「繼續確認」。`,
    cancelled: `已停止（確認了 ${done} 組日期）。`,
    failed: '連續幾次確認都失敗，先停下來。',
  }[v.stopReason] || '';
  $('vStatus').textContent = v.running
    ? `正在用虎航訂票頁確認班機時間：這一輪第 ${v.roundSearches} 組日期（最多 ${VERIFY_BUDGET} 組），會開背景分頁，查完自動關閉。`
    : status;

  const errs = [...new Set([...v.errors, ...(v.running ? v.roundErrors || [] : [])])];
  $('vWarnings').replaceChildren(...(errs.length
    ? [h('div', { class: 'alert warn', text: `部分日期確認失敗：${errs.join('；')}` })]
    : []));

  $('vTableWrap').hidden = !list.length;
  if (list.length) {
    $('vTable').tHead.replaceChildren(h('tr', {},
      round
        ? ['#', '目的地', '去程', '回程', '天數', '去程價', '回程價', '合計（含稅）', '']
          .map((t, i) => h('th', { class: i >= 5 && i <= 7 ? 'num' : '', text: t }))
        : ['#', '目的地', '出發', '票價（含稅）', ''].map((t, i) => h('th', { class: i === 3 ? 'num' : '', text: t }))));
    $('vTable').tBodies[0].replaceChildren(...list.map((t, i) => h('tr', { class: i === 0 ? 'cheapest' : '' },
      h('td', { class: 'rank', text: i + 1 }),
      h('td', {}, nameOf(t.dest), h('small', { text: ` ${t.dest}` })),
      h('td', {}, shortDate(t.depart), flightLine(t.out)),
      round && h('td', {}, shortDate(t.ret), flightLine(t.back)),
      round && h('td', { text: tripLength(t.days) }),
      round && h('td', { class: 'num', text: formatPrice(t.out.price) }),
      round && h('td', { class: 'num', text: formatPrice(t.back.price) }),
      h('td', { class: 'num total', text: `TWD ${formatPrice(t.total)}` }),
      h('td', {}, h('a', { class: 'book', href: bookingUrl(q.origin, t), target: '_blank', rel: 'noopener', text: '去訂票' })))));
  }

  $('vStop').hidden = !v.running;
  const more = !v.running && v.nextIndex < v.candidates.length && v.stopReason !== 'exhausted';
  $('vMore').hidden = !more;
  $('vMore').textContent = v.stopReason === 'bound' ? `再多找 ${VERIFY_KEEP} 組` : '繼續確認';
}

function setTimes(outFrom, outTo, backFrom, backTo) {
  [outFrom, outTo, backFrom, backTo].forEach((val, i) => { $(TIME_FIELDS[i]).value = val; });
  saveForm();
}

// ------------------------------------------------------------------- init ----
async function init() {
  renderOrigins();
  renderWeekdays();
  writeForm(await loadSavedForm());
  syncTripType();

  installHeaderRules().catch((e) => console.warn('header rules:', e));

  $('form').addEventListener('submit', search);
  // Remember what is on screen, not just what was last searched.
  $('form').addEventListener('change', saveForm);
  $('form').addEventListener('input', saveForm);
  for (const r of document.querySelectorAll('input[name="tripType"]')) r.addEventListener('change', syncTripType);
  $('origin').addEventListener('change', loadRoutes);
  $('extraCodes').addEventListener('input', updateDestCount);
  $('sortBy').addEventListener('change', () => { if (ui.query) { ui.shown = PAGE_SIZE; renderResults(); } saveForm(); });
  $('more').addEventListener('click', () => { ui.shown += PAGE_SIZE; renderResults(); });
  $('wkAll').addEventListener('click', () => setWeekdays([0, 1, 2, 3, 4, 5, 6]));
  $('wkWeekend').addEventListener('click', () => setWeekdays([4, 5, 6]));
  $('tmPreset').addEventListener('click', () => setTimes('00:00', '12:00', '14:00', '23:59'));
  $('tmClear').addEventListener('click', () => setTimes('', '', '', ''));
  $('vStop').addEventListener('click', () => { if (ui.verify) { ui.verify.cancelled = true; $('vStop').hidden = true; } });
  $('vMore').addEventListener('click', () => {
    const v = ui.verify;
    if (!v || v.running) return;
    if (v.stopReason === 'bound') v.keep += VERIFY_KEEP;
    runVerify();
  });

  await loadRoutes();
}

init();
