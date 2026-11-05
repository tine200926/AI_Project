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
