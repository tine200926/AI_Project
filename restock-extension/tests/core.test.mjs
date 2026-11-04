// node --test tests/core.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  classify, normalizeName, parseSale, regionCookie, resolveWatched, shopCountry,
  splitWatch, uniqueTargetId,
} from '../lib/core.js';

const page = (status, opts, extra = '') => `<html><head><title>BOYNEXTDOOR | Face Plush Keyring</title></head><body>
<script id="__NEXT_DATA__" type="application/json">{"props":{"sale":{"status":{"statusCode":"sale.status.${status}"},
"labelArtistInfo":{"id":7,"name":"BOYNEXTDOOR"},
"options":[${opts.map(([n, s]) => `{"saleStockId":1,"saleOptionName":${JSON.stringify(n)},"isSoldOut":${s}}`).join(',')}]}}}</script>
<script type="application/ld+json">{"@type":"Product","name":"Face Plush Keyring"}</script>${extra}</body></html>`;

test('parses options, status and name', () => {
  const p = parseSale(page('sale', [['O.C.L.', true], ['TAMTAM', false], ['Say "hi" ', false]]));
  assert.equal(p.statusCode, 'sale');
  assert.equal(p.statusKnown, true);
  assert.deepEqual([...p.options], [['O.C.L.', true], ['TAMTAM', false], ['Say "hi"', false]]);
  assert.equal(p.saleName, 'BOYNEXTDOOR / Face Plush Keyring');
  assert.equal(p.hasProduct, true);
});

test('non-sellable sale status overrides stale option flags', () => {
  const p = parseSale(page('out.of.stock', [['A', false], ['B', false]]));
  assert.deepEqual([...p.options.values()], [true, true]);
});

test('unknown status falls back to option flags', () => {
  const p = parseSale(page('brand.new', [['A', false]]));
  assert.equal(p.statusKnown, false);
  assert.equal(p.options.get('A'), false);
});

test('region-blocked page has no product', () => {
  const p = parseSale('<html><title>Weverse Shop</title></html>');
  assert.equal(p.options.size, 0);
  assert.equal(p.hasProduct, false);
});

test('country inference and cookies', () => {
  const u = 'https://shop.weverse.io/ja/shop/JPY/artists/7/sales/42095';
  assert.equal(shopCountry(u), 'JP');
  assert.equal(shopCountry(u, 'kr'), 'KR');
  assert.equal(shopCountry('https://shop.weverse.io/x/sales/1'), '');
  assert.equal(regionCookie(u),
    'wes_display_user_country=JP; wes_order_user_country=JP; wes_currency=JPY; wes_artistId=7');
  assert.equal(regionCookie('https://shop.weverse.io/x/sales/1'), '');
});

test('name matching: exact before prefix before contains', () => {
  const names = ['O.C.L.', 'O.C.L. BIG', '012 SUNGHO', '[6/30(月)受け取り]ACRYLIC STAND'];
  assert.deepEqual(resolveWatched(['o.c.l'], names).resolved, ['O.C.L.']);
  assert.deepEqual(resolveWatched(['sungho'], names).resolved, ['012 SUNGHO']);
  assert.deepEqual(resolveWatched(['acrylic stand'], names).resolved, ['[6/30(月)受け取り]ACRYLIC STAND']);
  assert.deepEqual(resolveWatched(['*'], names).resolved, names);
  assert.deepEqual(resolveWatched(['nope'], names), { resolved: [], missing: ['nope'] });
  assert.equal(normalizeName('O.C.L.'), 'OCL');
});

test('splitWatch accepts full-width separators', () => {
  assert.deepEqual(splitWatch('A, B，C、 D'), ['A', 'B', 'C', 'D']);
});

test('unique ids', () => {
  const u = 'https://shop.weverse.io/x/sales/42095';
  assert.equal(uniqueTargetId(u, []), '42095');
  assert.equal(uniqueTargetId(u, ['42095', '42095-2']), '42095-3');
});

test('classify transitions and cooldown', () => {
  const s = { notifyOnFirstRun: false, cooldownMinutes: 180 };
  const now = Date.parse('2026-11-04T12:00:00Z');
  assert.equal(classify(undefined, false, now, s).kind, 'new');
  assert.equal(classify(undefined, false, now, { ...s, notifyOnFirstRun: true }).kind, 'restock');
  assert.equal(classify({ soldOut: true }, false, now, s).kind, 'restock');
  assert.equal(classify({ soldOut: true, notifiedAt: '2026-11-04T11:00:00Z' }, false, now, s).kind, 'cooldown');
  assert.equal(classify({ soldOut: true, notifiedAt: '2026-11-04T08:00:00Z' }, false, now, s).kind, 'restock');
  assert.equal(classify({ soldOut: false }, true, now, s).kind, 'soldout');
  assert.equal(classify({ soldOut: false }, false, now, s).kind, 'same');
  assert.equal(classify({ soldOut: true }, true, now, s).kind, 'same');
});
