/**
 * Weverse 補貨監測 — Google 試算表後端
 *
 * 把這個檔案貼進試算表的「擴充功能 → Apps Script」，改掉 SECRET，
 * 執行一次 setup()，再「部署 → 新增部署作業 → 網頁應用程式」。
 * 詳細步驟見 README 的「用 Google 試算表管理監測清單」。
 *
 * 本機腳本用 GET 呼叫這個網頁應用程式：
 *   ?key=SECRET&action=read                         讀取 targets + settings
 *   ?key=SECRET&action=add&url=..&watch=..&label=.. 新增（網址已存在就更新並重新啟用）
 *   ?key=SECRET&action=remove&url=..                刪除該網址的列
 */

// 改成一串夠長的亂數，並把同一串填進 config.json 的 sheet.key。
const SECRET = 'CHANGE_ME_TO_A_LONG_RANDOM_STRING';

const TARGET_SHEET = 'targets';
const SETTINGS_SHEET = 'settings';

// 欄位用第一列的標題辨認，順序可以自己調；中英文標題都認得。
const ALIASES = {
  enabled: ['enabled', '啟用'],
  label:   ['label', '名稱'],
  url:     ['url', '網址'],
  watch:   ['watch', '選項', '監測選項'],
  country: ['country', '國家'],
};

const TARGET_HEADERS = [
  ['enabled', '打勾才監測；取消勾選 = 暫停，不用刪掉。空白 = 啟用'],
  ['label',   '顯示名稱，可留空'],
  ['url',     'Weverse Shop 商品頁網址'],
  ['watch',   '要監測的選項，用逗號分隔；留空或 * = 全部選項'],
  ['country', '（選填）國家代碼，例如 TW；留空會從網址判斷'],
  ['note',    '備註，程式不會讀'],
];

// 可以在 settings 工作表覆寫的參數（LINE token 等機密刻意不放這裡）。
const SETTING_DEFAULTS = [
  ['cooldownMinutes',   180,   '同一個選項補貨後，多久內不重複通知（分鐘）'],
  ['notifyOnFirstRun',  false, '第一次看到某選項就有貨時也通知'],
  ['notifyOnSoldOut',   false, '有貨 → 售完時也通知'],
  ['notifyOnError',     true,  '抓取失敗時通知'],
  ['intervalSeconds',   300,   '只有 -Loop 模式用：每次檢查間隔（秒）。排程模式的間隔在 install-task.ps1 設定'],
  ['requestTimeoutSec', 30,    '每個請求的逾時（秒）'],
  ['maxRetries',        3,     '失敗重試次數'],
  ['politeDelayMs',     1500,  '商品之間的間隔（毫秒），避免打太快'],
  ['logEveryOption',    false, 'log 裡記錄每個選項的狀態'],
];

// ------------------------------------------------------------ web app ----
function doGet(e) {
  const p = (e && e.parameter) || {};
  if (!SECRET || SECRET.indexOf('CHANGE_ME') === 0) {
    return out_({ ok: false, error: '請先在 Apps Script 裡把 SECRET 改成自己的亂數字串' });
  }
  if (p.key !== SECRET) return out_({ ok: false, error: 'key 不正確' });

  // 本機監測和 LINE webhook 可能同時呼叫，寫入前先上鎖。
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const action = p.action || 'read';
    if (action === 'read') {
      return out_({ ok: true, targets: readTargets_(), settings: readSettings_() });
    }
    if (action === 'add') return out_(Object.assign({ ok: true }, addTarget_(p)));
    if (action === 'remove') return out_({ ok: true, removed: removeTarget_(p.url) });
    return out_({ ok: false, error: 'unknown action: ' + action });
  } catch (err) {
    return out_({ ok: false, error: String((err && err.message) || err) });
  } finally {
    lock.releaseLock();
  }
}

function out_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// ------------------------------------------------------------- targets ----
function sheet_(name) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(name);
  if (!sh) throw new Error('找不到工作表「' + name + '」，請先在 Apps Script 執行 setup');
  return sh;
}

function headerMap_(sh) {
  const lastCol = sh.getLastColumn();
  const map = {};
  if (!lastCol) return map;
  sh.getRange(1, 1, 1, lastCol).getValues()[0].forEach(function (h, i) {
    const k = String(h).trim().toLowerCase();
    Object.keys(ALIASES).forEach(function (field) {
      if (!(field in map) && ALIASES[field].indexOf(k) >= 0) map[field] = i;
    });
  });
  if (!('url' in map)) throw new Error(sh.getName() + ' 工作表第一列需要有 url（或「網址」）欄');
  return map;
}

function dataRows_(sh) {
  const n = sh.getLastRow() - 1;
  if (n < 1) return [];
  return sh.getRange(2, 1, n, sh.getLastColumn()).getValues();
}

function cell_(row, map, field) {
  return field in map ? row[map[field]] : '';
}

function normalizeUrl_(u) {
  return String(u || '').trim().split(/[?#]/)[0];
}

function readTargets_() {
  const sh = sheet_(TARGET_SHEET);
  const m = headerMap_(sh);
  return dataRows_(sh)
    .map(function (r) {
      return {
        // 沒有 enabled 欄或格子是空的 = 啟用；未勾選的核取方塊是 false。
        enabled: 'enabled' in m ? r[m.enabled] : true,
        label:   String(cell_(r, m, 'label')).trim(),
        url:     normalizeUrl_(cell_(r, m, 'url')),
        watch:   String(cell_(r, m, 'watch')).trim(),
        country: String(cell_(r, m, 'country')).trim(),
      };
    })
    .filter(function (t) { return t.url; });
}

function addTarget_(p) {
  const url = normalizeUrl_(p.url);
  if (!url) throw new Error('缺少 url');
  const sh = sheet_(TARGET_SHEET);
  const m = headerMap_(sh);
  const rows = dataRows_(sh);

  for (let i = 0; i < rows.length; i++) {
    if (normalizeUrl_(rows[i][m.url]) === url) {
      const rowNo = i + 2;
      if ('watch' in m) sh.getRange(rowNo, m.watch + 1).setValue(p.watch || '*');
      if ('label' in m && !String(rows[i][m.label]).trim() && p.label) {
        sh.getRange(rowNo, m.label + 1).setValue(p.label);
      }
      if ('enabled' in m) sh.getRange(rowNo, m.enabled + 1).setValue(true);
      return { updated: true };
    }
  }

  const width = Math.max(sh.getLastColumn(), 1);
  const row = new Array(width).fill('');
  if ('enabled' in m) row[m.enabled] = true;
  if ('label' in m) row[m.label] = p.label || '';
  row[m.url] = url;
  if ('watch' in m) row[m.watch] = p.watch || '*';
  if ('country' in m && p.country) row[m.country] = p.country;
  sh.appendRow(row);
  if ('enabled' in m) {
    sh.getRange(sh.getLastRow(), m.enabled + 1).insertCheckboxes().setValue(true);
  }
  return { added: true };
}

function removeTarget_(rawUrl) {
  const url = normalizeUrl_(rawUrl);
  if (!url) throw new Error('缺少 url');
  const sh = sheet_(TARGET_SHEET);
  const m = headerMap_(sh);
  const rows = dataRows_(sh);
  let removed = 0;
  // 由下往上刪，列號才不會跑掉。
  for (let i = rows.length - 1; i >= 0; i--) {
    if (normalizeUrl_(rows[i][m.url]) === url) {
      sh.deleteRow(i + 2);
      removed++;
    }
  }
  return removed;
}

// ------------------------------------------------------------ settings ----
function readSettings_() {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SETTINGS_SHEET);
  const result = {};
  if (!sh) return result;
  dataRows_(sh).forEach(function (r) {
    const key = String(r[0]).trim();
    if (key && r[1] !== '' && r[1] !== null) result[key] = r[1];
  });
  return result;
}

// --------------------------------------------------------------- setup ----
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('補貨監測')
    .addItem('初始化工作表', 'setup')
    .addToUi();
}

/** 建立 targets / settings 兩個工作表（已存在的資料不會被覆蓋）。 */
function setup() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  let t = ss.getSheetByName(TARGET_SHEET);
  if (!t) t = ss.insertSheet(TARGET_SHEET);
  if (t.getLastRow() === 0) {
    const hdr = t.getRange(1, 1, 1, TARGET_HEADERS.length);
    hdr.setValues([TARGET_HEADERS.map(function (h) { return h[0]; })]).setFontWeight('bold');
    TARGET_HEADERS.forEach(function (h, i) { t.getRange(1, i + 1).setNote(h[1]); });
    t.appendRow([true, 'Face Plush Keyring',
      'https://shop.weverse.io/zh-tw/shop/KRW/artists/7/sales/42095', 'O.C.L', '', '範例，可以刪掉']);
    // 只在有資料的列放核取方塊：未勾選的核取方塊也算有內容，
    // 預先放一整欄會讓 appendRow 加到很下面去。
    t.getRange(2, 1).insertCheckboxes().setValue(true);
    t.setFrozenRows(1);
    t.setColumnWidth(3, 420);
    t.setColumnWidth(4, 220);
  }

  let s = ss.getSheetByName(SETTINGS_SHEET);
  if (!s) s = ss.insertSheet(SETTINGS_SHEET);
  if (s.getLastRow() === 0) {
    s.getRange(1, 1, 1, 3).setValues([['key', 'value', '說明']]).setFontWeight('bold');
    s.getRange(2, 1, SETTING_DEFAULTS.length, 3).setValues(SETTING_DEFAULTS);
    SETTING_DEFAULTS.forEach(function (d, i) {
      if (typeof d[1] === 'boolean') {
        s.getRange(i + 2, 2).insertCheckboxes().setValue(d[1]);
      }
    });
    s.setFrozenRows(1);
    s.setColumnWidth(1, 170);
    s.setColumnWidth(3, 480);
  }

  // 新試算表預設的空白「工作表1」用不到就移掉。
  ['工作表1', 'Sheet1'].forEach(function (n) {
    const sh = ss.getSheetByName(n);
    if (sh && ss.getSheets().length > 1 && sh.getLastRow() === 0) ss.deleteSheet(sh);
  });
}
