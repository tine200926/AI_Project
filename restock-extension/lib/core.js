// Pure helpers ported from the PowerShell monitor (weverse-restock/lib.ps1).
// No chrome.* calls here, so the logic can be unit-tested under node.

// Region-restricted sales (JP-only merch, etc.) render an empty page unless
// the request claims to be from that country. Infer it from the currency in
// the URL; an explicit per-target country always wins.
const CURRENCY_COUNTRY = { JPY: 'JP', KRW: 'KR', USD: 'US', TWD: 'TW' };

export function shopCountry(url, country = '') {
  if (country && country.trim()) return country.trim().toUpperCase();
  const m = url.match(/\/shop\/([A-Z]{3})\//);
  return (m && CURRENCY_COUNTRY[m[1]]) || '';
}

// Both country cookies are required: setting only wes_display_user_country
// still returns the "cannot be viewed in your country" shell.
export function regionCookie(url, country = '') {
  const c = shopCountry(url, country);
  if (!c) return '';
  const parts = [`wes_display_user_country=${c}`, `wes_order_user_country=${c}`];
  let m;
  if ((m = url.match(/\/shop\/([A-Z]{3})\//))) parts.push(`wes_currency=${m[1]}`);
  if ((m = url.match(/\/artists\/(\d+)\//))) parts.push(`wes_artistId=${m[1]}`);
  return parts.join('; ');
}

export function normalizeUrl(url) {
  return String(url || '').trim().split(/[?#]/)[0];
}

export function isShopUrl(url) {
  return /^https:\/\/shop\.weverse\.io\//.test(url);
}

// Short, stable key for saved state. Weverse URLs end in the sale id.
export function targetBaseId(url) {
  const m = url.match(/\/sales\/(\d+)/);
  return m ? m[1] : url.replace(/[^A-Za-z0-9]/g, '_');
}

export function uniqueTargetId(url, existingIds) {
  const base = targetBaseId(url);
  if (!existingIds.includes(base)) return base;
  for (let n = 2; ; n++) {
    if (!existingIds.includes(`${base}-${n}`)) return `${base}-${n}`;
  }
}

function unescapeJsonString(s) {
  try {
    return JSON.parse(`"${s}"`);
  } catch {
    return s.replace(/\\"/g, '"').replace(/\\\//g, '/').replace(/\\\\/g, '\\');
  }
}

// The sale-level status overrides the per-option flags, which go stale: a
// sale sitting at SOLD_OUT can still carry "isSoldOut":false options that are
// not actually buyable.
const NOT_SELLABLE = new Set(['out.of.stock', 'end', 'ended', 'closed', 'stop', 'to.be.sold']);

/**
 * Parse a Weverse Shop sale page.
 * Weverse renders the sale payload into the HTML (__NEXT_DATA__), so one plain
 * GET is enough: no login, no headless browser.
 * @returns {{options: Map<string, boolean>, statusCode: string,
 *            statusKnown: boolean, hasProduct: boolean, saleName: string}}
 *          options maps option name -> isSoldOut.
 */
export function parseSale(html) {
  const options = new Map();
  const re = /"saleOptionName":"((?:[^"\\]|\\.)*)","isSoldOut":(true|false)/g;
  for (const m of html.matchAll(re)) {
    // Single-SKU sales carry one pseudo-option named after the product, often
    // with a trailing space; trim so it does not leak into messages and keys.
    const name = unescapeJsonString(m[1]).trim() || '(單一規格)';
    options.set(name, m[2] === 'true');
  }

  const sm = html.match(/"statusCode":"sale\.status\.([a-z.]+)"/);
  const statusCode = sm ? sm[1] : '';
  let statusKnown = true;
  if (statusCode === 'sale') {
    // purchasable: trust the per-option flags
  } else if (NOT_SELLABLE.has(statusCode)) {
    for (const k of options.keys()) options.set(k, true);
  } else {
    // Unknown or missing: never go silently blind if Weverse renames a
    // status - keep using the per-option flags, and let the caller warn.
    statusKnown = false;
  }

  const hasProduct = html.includes('"@type":"Product"');

  // Product name for auto-labels: the schema.org JSON-LD "name", or the part
  // of <title> after "ARTIST | ".
  let product = '';
  let artist = '';
  let m;
  if ((m = html.match(/"@type":"Product","name":"((?:[^"\\]|\\.)*)"/))) {
    product = unescapeJsonString(m[1]);
  } else if ((m = html.match(/<title>([^<]+)<\/title>/))) {
    const parts = m[1].split(/\s*\|\s*/);
    product = parts.length >= 2 ? parts.slice(1).join(' | ') : parts[0];
  }
  if ((m = html.match(/"labelArtistInfo":\{[^}]*?"name":"((?:[^"\\]|\\.)*)"/))) {
    artist = unescapeJsonString(m[1]);
  }
  product = product.trim();
  artist = artist.trim();
  const saleName = artist && product ? `${artist} / ${product}` : product;

  return { options, statusCode, statusKnown, hasProduct, saleName };
}

/** Error text for a page that yielded no options. */
export function noOptionsReason(parsed, country) {
  if (!parsed.hasProduct) {
    return country
      ? `頁面沒有商品資料，通常是這個商品不開放「${country}」地區瀏覽。如果是其他地區限定，請在設定裡填該商品的國家代碼。`
      : '頁面沒有商品資料，可能是地區限定商品。請在設定裡填該商品的國家代碼（例如 JP）。';
  }
  return '頁面上找不到任何選項：商品可能已下架，或 Weverse 改了網頁格式。';
}

// The UI shows "O.C.L" but the payload says "O.C.L."; dropping case and
// punctuation keeps the watch list forgiving to type.
export function normalizeName(name) {
  return String(name).replace(/[^\p{L}\p{N}]/gu, '').toUpperCase();
}

/**
 * Map each watch-list entry to the real option name(s) on the page.
 * A '*' entry (or an empty list) watches every option. Tightest match wins:
 * exact, then prefix, then substring - so "O.C.L" cannot silently also select
 * a longer unrelated option, while "[6/30受け取り]ACRYLIC STAND" still matches
 * "ACRYLIC STAND".
 * @returns {{resolved: string[], missing: string[]}}
 */
export function resolveWatched(watch, optionNames) {
  const names = [...optionNames];
  if (!watch.length || watch.includes('*')) return { resolved: names, missing: [] };

  const resolved = new Set();
  const missing = [];
  for (const wanted of watch) {
    const target = normalizeName(wanted);
    if (!target) continue;
    let hit = false;
    for (const mode of ['exact', 'prefix', 'contains']) {
      for (const actual of names) {
        const c = normalizeName(actual);
        const ok = mode === 'exact' ? c === target
          : mode === 'prefix' ? c.startsWith(target)
            : c.includes(target);
        if (ok) { resolved.add(actual); hit = true; }
      }
      if (hit) break;
    }
    if (!hit) missing.push(wanted);
  }
  // Keep page order so notifications read naturally.
  return { resolved: names.filter((n) => resolved.has(n)), missing };
}

/** Split a typed option list on ASCII/full-width commas and 、. */
export function splitWatch(text) {
  return String(text || '').split(/[,，、\n]/).map((s) => s.trim()).filter(Boolean);
}

/**
 * Decide what one watched option's new reading means.
 * prev: saved {soldOut, notifiedAt} or undefined.
 * @returns {{kind: 'restock'|'cooldown'|'soldout'|'new'|'same'}}
 */
export function classify(prev, soldOut, now, settings) {
  const firstSighting = !prev || prev.soldOut === null || prev.soldOut === undefined;
  const available = !soldOut;
  const isRestock = available && (firstSighting ? !!settings.notifyOnFirstRun : prev.soldOut);
  if (isRestock) {
    const last = prev && prev.notifiedAt ? Date.parse(prev.notifiedAt) : NaN;
    const cooled = Number.isNaN(last) || (now - last) / 60000 >= settings.cooldownMinutes;
    return { kind: cooled ? 'restock' : 'cooldown' };
  }
  if (soldOut && !firstSighting && !prev.soldOut) return { kind: 'soldout' };
  if (firstSighting) return { kind: 'new' };
  return { kind: 'same' };
}
