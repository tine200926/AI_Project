// node --test tests/core.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  addDays, bookingUrl, cheapestPerDestination, collectStations, daysBetween, findTrips,
  isIsoDate, localToday, mergeFares, monthChunks, normalizeQuery, parseCodes,
  parseDailyPrices, planLegs, planRequests, runPool, shortDate, sortTrips, tripLength, weekday,
} from '../lib/core.js';

const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];

function query(over = {}) {
  return {
    tripType: 'roundTrip', origin: 'TPE', destinations: ['KIX'],
    start: '2026-12-01', end: '2026-12-20', minDays: 3, maxDays: 5,
    minPrice: null, maxPrice: null, weekdays: ALL_DAYS, ...over,
  };
}

const fares = (out, back = {}) => ({ out: new Map(Object.entries(out)), back: new Map(Object.entries(back)) });

test('dates', () => {
  assert.equal(addDays('2026-12-30', 3), '2027-01-02');
  assert.equal(addDays('2028-02-28', 1), '2028-02-29');
  assert.equal(addDays('2026-03-01', -1), '2026-02-28');
  assert.equal(daysBetween('2026-12-01', '2026-12-05'), 4);
  assert.equal(weekday('2026-11-05'), 4); // Thursday
  assert.equal(shortDate('2026-12-05'), '12/05（六）');
  assert.equal(isIsoDate('2026-02-29'), false);
  assert.equal(isIsoDate('2028-02-29'), true);
  assert.equal(isIsoDate('2026-1-1'), false);
  assert.equal(localToday(new Date(2026, 10, 5, 23, 59)), '2026-11-05');
  assert.equal(tripLength(5), '5天4夜');
  assert.equal(tripLength(1), '當天來回');
});

test('monthChunks splits at month boundaries', () => {
  assert.deepEqual(monthChunks('2026-11-20', '2027-01-10'), [
    { since: '2026-11-20', until: '2026-11-30' },
    { since: '2026-12-01', until: '2026-12-31' },
    { since: '2027-01-01', until: '2027-01-10' },
  ]);
  assert.deepEqual(monthChunks('2026-12-05', '2026-12-05'), [{ since: '2026-12-05', until: '2026-12-05' }]);
  assert.deepEqual(monthChunks('2026-12-06', '2026-12-05'), []);
});

test('parseCodes', () => {
  assert.deepEqual(parseCodes(' nrt, kix、oka  xx3 toolong ab'), ['NRT', 'KIX', 'OKA', 'XX3']);
});

test('normalizeQuery accepts a good form and clamps the past', () => {
  const { query: q, errors } = normalizeQuery({
    tripType: 'roundTrip', origin: 'tpe', destinations: ['KIX', 'kix', 'TPE', 'NRT'],
    start: '2026-11-01', end: '2026-12-31', minDays: '3', maxDays: '5',
    minPrice: '', maxPrice: '8,000', weekdays: ['5', '6', 6],
  }, '2026-11-05');
  assert.deepEqual(errors, []);
  assert.equal(q.origin, 'TPE');
  assert.deepEqual(q.destinations, ['KIX', 'NRT']); // deduped, origin dropped
  assert.equal(q.start, '2026-11-05'); // clamped to today
  assert.equal(q.minPrice, null);
  assert.equal(q.maxPrice, 8000);
  assert.deepEqual(q.weekdays, [5, 6]);
});

test('normalizeQuery reports problems', () => {
  const bad = (over) => normalizeQuery({
    tripType: 'roundTrip', origin: 'TPE', destinations: ['KIX'], start: '2026-12-01', end: '2026-12-10',
    minDays: 3, maxDays: 5, minPrice: '', maxPrice: '', weekdays: ALL_DAYS, ...over,
  }, '2026-11-05').errors;
  assert.match(bad({ destinations: [] })[0], /目的地/);
  assert.match(bad({ end: '2026-11-30' })[0], /結束日期/);
  assert.match(bad({ end: '2026-10-01' })[0], /結束日期/); // whole window in the past
  assert.match(bad({ minDays: 6, maxDays: 4 })[0], /上限/);
  assert.match(bad({ minDays: 12, maxDays: 14 })[0], /比最少旅遊天數/);
  assert.match(bad({ minPrice: '5000', maxPrice: '3000' })[0], /價錢上限/);
  assert.match(bad({ maxPrice: 'abc' })[0], /數字/);
  assert.match(bad({ weekdays: [] })[0], /星期/);
  assert.match(bad({ end: '2028-01-01' })[0], /最長/);
  // One-way ignores the day counts entirely.
  assert.deepEqual(bad({ tripType: 'oneWay', minDays: 12, maxDays: 1 }), []);
  // Blank max days means "exactly min days".
  const r = normalizeQuery({
    tripType: 'roundTrip', origin: 'TPE', destinations: ['KIX'], start: '2026-12-01', end: '2026-12-10',
    minDays: 4, maxDays: '', weekdays: ALL_DAYS,
  }, '2026-11-05');
  assert.deepEqual(r.errors, []);
  assert.equal(r.query.maxDays, 4);
});

test('planLegs trims days a trip of minDays cannot use', () => {
  assert.deepEqual(planLegs(query({ destinations: ['KIX'] })), [
    { dest: 'KIX', leg: 'out', origin: 'TPE', destination: 'KIX', since: '2026-12-01', until: '2026-12-18' },
    { dest: 'KIX', leg: 'back', origin: 'KIX', destination: 'TPE', since: '2026-12-03', until: '2026-12-20' },
  ]);
  assert.deepEqual(planLegs(query({ tripType: 'oneWay', destinations: ['NRT'] })), [
    { dest: 'NRT', leg: 'out', origin: 'TPE', destination: 'NRT', since: '2026-12-01', until: '2026-12-20' },
  ]);
  const reqs = planRequests(query({ start: '2026-11-25', end: '2027-01-05', destinations: ['KIX', 'OKA'] }));
  // 2 destinations x 2 directions x (Nov, Dec, Jan)
  assert.equal(reqs.length, 12);
  assert.deepEqual(reqs[0], { dest: 'KIX', leg: 'out', origin: 'TPE', destination: 'KIX', since: '2026-11-25', until: '2026-11-30' });
});

test('parseDailyPrices handles the REST and GraphQL shapes', () => {
  const rest = parseDailyPrices({ data: [
    { date: '2026-12-01', amount: 1799 },
    { date: '2026-12-02T00:00:00', amount: 1599 },
    { date: '2026-12-03', amount: 0 }, // no flight
    { date: '2026-12-04', amount: null },
    { date: 'bad', amount: 10 },
    { date: '2026-12-01', amount: 1699 }, // duplicate: lower wins
    null,
  ] });
  assert.deepEqual([...rest.prices], [['2026-12-01', 1699], ['2026-12-02', 1599]]);

  const gql = parseDailyPrices({ data: { appLiveDailyPrices: [
    { origin: 'FUK', destination: 'XX3', date: '2023-12-19', currency: 'TWD', amount: 7199, fareLabels: [] },
  ] } });
  assert.deepEqual([...gql.prices], [['2023-12-19', 7199]]);
  assert.equal(gql.currency, 'TWD');

  assert.equal(parseDailyPrices({ data: [] }).prices.size, 0);
  assert.throws(() => parseDailyPrices({ message: 'Invalid origin' }), /Invalid origin/);
  assert.throws(() => parseDailyPrices({ errors: [{ message: 'boom' }] }), /boom/);
  assert.throws(() => parseDailyPrices({ foo: 1 }), /格式/);
  assert.throws(() => parseDailyPrices(null), /格式/);
});

test('collectStations walks any nesting', () => {
  const json = { data: { zh: [
    { country: { code2: 'JP' }, stationMenus: [
      { station: { stationCode: 'NRT', stationName: '東京(成田)' } },
      { station: { stationCode: 'kix' } },
    ] },
    { country: { code2: 'XX' }, stationMenus: [{ station: { stationCode: 'XX9', name: '測試城市' } }, { station: { stationCode: 'TOOLONG' } }] },
  ] } };
  assert.deepEqual([...collectStations(json)], [['NRT', '東京(成田)'], ['KIX', ''], ['XX9', '測試城市']]);
  assert.equal(collectStations({}).size, 0);
});

test('mergeFares keeps only dates inside each request and the cheaper duplicate', () => {
  const reqs = [
    { dest: 'KIX', leg: 'out', since: '2026-12-01', until: '2026-12-31' },
    { dest: 'KIX', leg: 'back', since: '2026-12-03', until: '2026-12-31' },
    { dest: 'OKA', leg: 'out', since: '2026-12-01', until: '2026-12-31' },
  ];
  const ok = (pairs) => ({ ok: true, value: { prices: new Map(pairs) } });
  const merged = mergeFares(reqs, [
    ok([['2026-11-30', 1], ['2026-12-01', 1999], ['2027-01-01', 1]]),
    ok([['2026-12-02', 1], ['2026-12-05', 2299]]),
    { ok: false, error: new Error('x') },
  ]);
  assert.deepEqual([...merged.KIX.out], [['2026-12-01', 1999]]);
  assert.deepEqual([...merged.KIX.back], [['2026-12-05', 2299]]);
  assert.equal(merged.OKA, undefined);
});

test('findTrips combines round trips within days, window, price and weekday', () => {
  const f = {
    KIX: fares(
      { '2026-12-01': 2000, '2026-12-04': 1500, '2026-12-18': 900 },
      { '2026-12-03': 1800, '2026-12-05': 2500, '2026-12-08': 1000, '2026-12-20': 700, '2026-12-22': 1 },
    ),
  };
  const trips = findTrips(query(), f);
  assert.deepEqual(trips.map((t) => [t.depart, t.ret, t.days, t.total]), [
    ['2026-12-18', '2026-12-20', 3, 1600], // 900 + 700
    ['2026-12-04', '2026-12-08', 5, 2500], // 1500 + 1000
    ['2026-12-01', '2026-12-03', 3, 3800],
    ['2026-12-01', '2026-12-05', 5, 4500],
  ]);
  // 12-22 is past the window end, 12-04 -> 12-05 is only 2 days: neither appears.

  assert.deepEqual(findTrips(query({ maxPrice: 2500 }), f).map((t) => t.total), [1600, 2500]);
  assert.deepEqual(findTrips(query({ minPrice: 2000, maxPrice: 4000 }), f).map((t) => t.total), [2500, 3800]);
  // Only Fridays (12/04 and 12/18 are Fridays, 12/01 is a Tuesday).
  assert.deepEqual(findTrips(query({ weekdays: [5] }), f).map((t) => t.depart), ['2026-12-18', '2026-12-04']);
  // Exactly 3 days.
  assert.deepEqual(findTrips(query({ minDays: 3, maxDays: 3 }), f).map((t) => t.total), [1600, 3800]);
  // Day trips.
  const day = { KIX: fares({ '2026-12-10': 800 }, { '2026-12-10': 900 }) };
  assert.deepEqual(findTrips(query({ minDays: 1, maxDays: 1 }), day).map((t) => [t.days, t.total]), [[1, 1700]]);
});

test('findTrips one-way and multiple destinations', () => {
  const f = {
    KIX: fares({ '2026-12-02': 1999, '2026-12-25': 999 }),
    OKA: fares({ '2026-12-02': 1499, '2026-12-03': 1499 }),
  };
  const q = query({ tripType: 'oneWay', destinations: ['KIX', 'OKA', 'NRT'], minDays: null, maxDays: null });
  const trips = findTrips(q, f);
  // 12-25 is outside the window.
  assert.deepEqual(trips.map((t) => [t.dest, t.depart, t.total, t.ret]), [
    ['OKA', '2026-12-02', 1499, null],
    ['OKA', '2026-12-03', 1499, null],
    ['KIX', '2026-12-02', 1999, null],
  ]);
  assert.deepEqual(cheapestPerDestination(trips).map((t) => [t.dest, t.total]), [['OKA', 1499], ['KIX', 1999]]);
  assert.deepEqual(sortTrips(trips, 'date').map((t) => t.dest + t.depart.slice(8)), ['OKA02', 'KIX02', 'OKA03']);
});

test('bookingUrl', () => {
  const rt = new URL(bookingUrl('TPE', { dest: 'KIX', depart: '2026-12-04', ret: '2026-12-08' }));
  assert.equal(rt.origin, 'https://booking.tigerairtw.com');
  assert.equal(rt.searchParams.get('type'), 'roundTrip');
  assert.equal(rt.searchParams.get('outbound'), 'TPE-KIX');
  assert.equal(rt.searchParams.get('inbound'), 'KIX-TPE');
  assert.equal(rt.searchParams.get('departureDate'), '2026-12-04');
  assert.equal(rt.searchParams.get('returnDate'), '2026-12-08');
  assert.equal(rt.searchParams.get('adult'), '1');
  const ow = new URL(bookingUrl('KHH', { dest: 'OKA', depart: '2026-12-04', ret: null }));
  assert.equal(ow.searchParams.get('type'), 'oneWay');
  assert.equal(ow.searchParams.get('outbound'), 'KHH-OKA');
  assert.equal(ow.searchParams.has('inbound'), false);
  assert.equal(ow.searchParams.has('returnDate'), false);
});

test('runPool limits concurrency, keeps order and never rejects', async () => {
  let live = 0;
  let peak = 0;
  const progress = [];
  const tasks = [30, 5, 20, 1, 10].map((ms, i) => async () => {
    live++; peak = Math.max(peak, live);
    await new Promise((r) => setTimeout(r, ms));
    live--;
    if (i === 2) throw new Error('nope');
    return i;
  });
  const res = await runPool(tasks, 2, (d, t) => progress.push(`${d}/${t}`));
  assert.equal(peak, 2);
  assert.deepEqual(res.map((r) => (r.ok ? r.value : r.error.message)), [0, 1, 'nope', 3, 4]);
  assert.deepEqual(progress, ['1/5', '2/5', '3/5', '4/5', '5/5']);
  assert.deepEqual(await runPool([], 3), []);
});

// ------------------------------------------------------- departure times ----
import { hasTimeFilter, legKey, legUrl, parseFlightSearch, pickFlight, verifyTrips } from '../lib/core.js';

test('normalizeQuery departure-time windows', () => {
  const base = {
    tripType: 'roundTrip', origin: 'TPE', destinations: ['KIX'], start: '2026-12-01', end: '2026-12-10',
    minDays: 3, maxDays: 5, weekdays: ALL_DAYS,
  };
  const run = (over) => normalizeQuery({ ...base, ...over }, '2026-11-05');
  let r = run({});
  assert.equal(r.query.outTime, null);
  assert.equal(r.query.backTime, null);
  assert.equal(hasTimeFilter(r.query), false);

  r = run({ outTimeFrom: '06:00', outTimeTo: '12:00', backTimeFrom: '15:00', backTimeTo: '' });
  assert.deepEqual(r.errors, []);
  assert.deepEqual(r.query.outTime, { from: '06:00', to: '12:00' });
  assert.deepEqual(r.query.backTime, { from: '15:00', to: '23:59' });
  assert.equal(hasTimeFilter(r.query), true);

  // A whole-day window is no filter; seconds from <input type=time step=1> are dropped.
  r = run({ outTimeFrom: '00:00', outTimeTo: '23:59', backTimeTo: '10:30:00' });
  assert.equal(r.query.outTime, null);
  assert.deepEqual(r.query.backTime, { from: '00:00', to: '10:30' });

  assert.match(run({ outTimeFrom: '12:00', outTimeTo: '08:00' }).errors[0], /去程出發時間的結束時間/);
  assert.match(run({ backTimeFrom: '25:00' }).errors[0], /回程出發時間請用/);
  // One-way ignores the return window.
  r = run({ tripType: 'oneWay', backTimeFrom: '25:00', outTimeFrom: '18:00' });
  assert.deepEqual(r.errors, []);
  assert.equal(r.query.backTime, null);
  assert.deepEqual(r.query.outTime, { from: '18:00', to: '23:59' });
});

function seg(o, d, date, dep, arr, no) {
  return { origin: o, destination: d, departureTime: `${date} ${dep}:00`, arrivalTime: `${date} ${arr}:00`, carrierCode: 'IT', flightNumber: ` ${no}` };
}
function fare(cls, total, { discounted = 0, sellable = true, count = 9 } = {}) {
  return { sellable, productClass: cls, availableCount: count,
    paxFares: [{ paxType: 'CHD', ticketPrice: { totalAmount: 1 } }, { paxType: 'ADT', ticketPrice: { totalAmount: total, discountedTotalAmount: discounted, userCurrency: 'TWD' } }] };
}

const SEARCH_JSON = { data: { appFlightSearchResult: { journeys: [
  { legs: [{ availabilityLegs: [
    { availabilitySegments: [seg('TPE', 'KIX', '2026-12-04', '13:30', '17:05', 202)], fares: [fare('tigersmart', 4100), fare('tigerlight', 3200)] },
    { availabilitySegments: [seg('TPE', 'KIX', '2026-12-04', '06:50', '10:20', 200)], fares: [fare('tigerlight', 3900, { discounted: 3500 })] },
    { availabilitySegments: [seg('TPE', 'KIX', '2026-12-04', '21:00', '00:35', 206)], fares: [fare('tigerlight', 2100, { sellable: false })] },
  ] }] },
  { legs: [{ availabilityLegs: [
    { availabilitySegments: [seg('KIX', 'TPE', '2026-12-08', '18:40', '20:45', 203)], fares: [fare('tigerlight', 2800)] },
  ] }] },
] } } };

test('parseFlightSearch reads flights, times and the cheapest sellable adult fare', () => {
  const legs = parseFlightSearch(SEARCH_JSON);
  assert.deepEqual(legs.map(legKey), ['TPE-KIX-2026-12-04', 'KIX-TPE-2026-12-08']);
  assert.deepEqual(legs[0].flights.map((f) => [f.flightNo, f.dep, f.arr, f.price, f.fareClass]), [
    ['IT200', '06:50', '10:20', 3500, 'tigerlight'], // discounted price wins
    ['IT202', '13:30', '17:05', 3200, 'tigerlight'], // cheapest family
    ['IT206', '21:00', '00:35', null, ''], // nothing sellable
  ]);
  assert.equal(legs[1].flights[0].price, 2800);
  assert.deepEqual(parseFlightSearch({ data: { appFlightSearchResult: { journeys: [] } } }), []);
  assert.throws(() => parseFlightSearch({ data: { other: 1 } }), /不是虎航/);
});

test('pickFlight respects the window and skips sold-out flights', () => {
  const flights = parseFlightSearch(SEARCH_JSON)[0].flights;
  assert.equal(pickFlight(flights, null).flightNo, 'IT202');
  assert.equal(pickFlight(flights, { from: '00:00', to: '12:00' }).flightNo, 'IT200');
  assert.equal(pickFlight(flights, { from: '20:00', to: '23:59' }), null); // IT206 is sold out
  assert.equal(pickFlight(flights, { from: '13:30', to: '13:30' }).flightNo, 'IT202'); // inclusive
  assert.equal(pickFlight(null, null), null);
});

test('legUrl', () => {
  const u = new URL(legUrl({ origin: 'KIX', destination: 'TPE', date: '2026-12-08' }));
  assert.equal(u.searchParams.get('type'), 'oneWay');
  assert.equal(u.searchParams.get('outbound'), 'KIX-TPE');
  assert.equal(u.searchParams.get('departureDate'), '2026-12-08');
});

// A fake booking site: flights at 07:00 / 13:00 / 19:00, each costing the
// calendar price plus a tax and a per-flight surcharge.
function fakeWorld(calendar, surcharge = { '07:00': 300, '13:00': 0, '19:00': 600 }) {
  const calls = [];
  const flightsFor = (o, d, date) => {
    const base = calendar[`${o}-${d}-${date}`];
    if (!base) return [];
    return Object.entries(surcharge).map(([dep, extra], i) => ({
      flightNo: `IT${i}`, dep, arr: '', stops: 0, price: base + 200 + extra,
    }));
  };
  const search = async (missing) => {
    calls.push(missing.map(legKey).join(' + '));
    return missing.map((l) => ({ ...l, flights: flightsFor(l.origin, l.destination, l.date) }));
  };
  return { calls, search };
}

function tripsFrom(calendar, q) {
  const fares = { KIX: { out: new Map(), back: new Map() } };
  for (const [k, v] of Object.entries(calendar)) {
    const [o, , y, m, d] = k.split('-');
    (o === 'TPE' ? fares.KIX.out : fares.KIX.back).set(`${y}-${m}-${d}`, v);
  }
  return findTrips(q, fares);
}

test('verifyTrips finds the cheapest time-matching trips and stops at the bound', async () => {
  const calendar = {
    'TPE-KIX-2026-12-01': 1000, 'TPE-KIX-2026-12-02': 1100, 'TPE-KIX-2026-12-03': 3000,
    'KIX-TPE-2026-12-04': 1000, 'KIX-TPE-2026-12-05': 1050, 'KIX-TPE-2026-12-06': 3000,
  };
  const q = query({ start: '2026-12-01', end: '2026-12-06', minDays: 4, maxDays: 4, outTime: { from: '06:00', to: '10:00' }, backTime: null });
  const candidates = tripsFrom(calendar, q);
  assert.deepEqual(candidates.map((c) => `${c.depart.slice(8)}-${c.ret.slice(8)}:${c.total}`), ['01-04:2000', '02-05:2150', '03-06:6000']);

  const { calls, search } = fakeWorld(calendar);
  const legCache = new Map();
  const updates = [];
  const r = await verifyTrips({ q, candidates, legCache, search, keep: 1, onUpdate: (u) => updates.push(u.searches) });
  // Outbound must leave 06:00-10:00 -> the 07:00 flight (+300); return any time -> 13:00 (+0).
  assert.equal(r.verified[0].total, 1000 + 200 + 300 + 1000 + 200);
  assert.equal(r.verified[0].out.dep, '07:00');
  assert.equal(r.verified[0].back.dep, '13:00');
  assert.equal(r.verified[0].estimate, 2000);
  // 2nd candidate's calendar total (2150) < 2700, so it is checked; the 3rd (6000) is not.
  assert.equal(r.stopReason, 'bound');
  assert.equal(r.nextIndex, 2);
  assert.equal(r.searches, 2);
  assert.deepEqual(calls, ['TPE-KIX-2026-12-01 + KIX-TPE-2026-12-04', 'TPE-KIX-2026-12-02 + KIX-TPE-2026-12-05']);
  assert.ok(updates.length >= 2);

  // Continue with a bigger keep: only the third candidate is new.
  const r2 = await verifyTrips({ q, candidates, legCache, search, keep: 5, startIndex: r.nextIndex, verified: r.verified });
  assert.equal(r2.stopReason, 'exhausted');
  assert.equal(r2.verified.length, 3);
  assert.equal(calls.length, 3);
});

test('verifyTrips reuses legs, honours budget, price range, failures and cancel', async () => {
  const calendar = {
    'TPE-KIX-2026-12-01': 1000, 'KIX-TPE-2026-12-03': 1000, 'KIX-TPE-2026-12-04': 1100, 'KIX-TPE-2026-12-05': 1200,
  };
  const q = query({ start: '2026-12-01', end: '2026-12-05', minDays: 3, maxDays: 5, outTime: { from: '18:00', to: '23:59' }, backTime: null });
  const candidates = tripsFrom(calendar, q);
  assert.equal(candidates.length, 3); // same outbound day, three return days

  let world = fakeWorld(calendar);
  let r = await verifyTrips({ q, candidates, legCache: new Map(), search: world.search, keep: 5 });
  // The outbound leg is looked up once, then reused (one-leg searches after that).
  assert.deepEqual(world.calls, [
    'TPE-KIX-2026-12-01 + KIX-TPE-2026-12-03', 'KIX-TPE-2026-12-04', 'KIX-TPE-2026-12-05',
  ]);
  assert.deepEqual(r.verified.map((t) => t.out.dep), ['19:00', '19:00', '19:00']);

  // Budget.
  world = fakeWorld(calendar);
  r = await verifyTrips({ q, candidates, legCache: new Map(), search: world.search, keep: 5, maxSearches: 1 });
  assert.equal(r.stopReason, 'budget');
  assert.equal(r.verified.length, 1);
  assert.equal(r.nextIndex, 1);

  // Price range applies to real prices: 1000+200+600 + 1000+200 = 3000.
  world = fakeWorld(calendar);
  r = await verifyTrips({ q: { ...q, maxPrice: 3000 }, candidates, legCache: new Map(), search: world.search });
  assert.deepEqual(r.verified.map((t) => t.total), [3000]);

  // No flight in the window at all.
  world = fakeWorld(calendar, { '07:00': 0 });
  r = await verifyTrips({ q, candidates, legCache: new Map(), search: world.search });
  assert.equal(r.stopReason, 'exhausted');
  assert.equal(r.verified.length, 0);
  // The outbound day has nothing in the window, so its other trips are skipped unsearched.
  assert.equal(world.calls.length, 1);

  // Three failures in a row stop the run; a failed leg is not retried.
  let n = 0;
  r = await verifyTrips({ q: { ...q, outTime: null }, candidates, legCache: new Map(), search: async () => { n++; throw new Error('逾時'); } });
  assert.equal(r.stopReason, 'exhausted'); // later candidates share the failed outbound leg
  assert.equal(n, 1);
  assert.deepEqual(r.errors, ['逾時']);
  n = 0;
  const many = [1, 2, 3, 4].map((d) => ({ dest: 'KIX', depart: `2026-12-0${d}`, ret: null, total: 1000 + d }));
  r = await verifyTrips({ q: { ...q, tripType: 'oneWay' }, candidates: many, legCache: new Map(), search: async () => { n++; throw new Error('x'); } });
  assert.equal(r.stopReason, 'failed');
  assert.equal(n, 3);
  assert.equal(r.nextIndex, 3);

  // Cancel before anything runs.
  r = await verifyTrips({ q, candidates, legCache: new Map(), search: world.search, isCancelled: () => true });
  assert.equal(r.stopReason, 'cancelled');
  assert.equal(r.nextIndex, 0);
});
