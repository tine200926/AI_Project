const C = globalThis.CouponCore;
const $ = (id) => document.getElementById(id);
const NUM = ['leadSec', 'durationSec', 'clickIntervalMs', 'refreshIntervalMs', 'advanceMs'];
const BOOL = ['refreshAtStart', 'timeSync', 'autoConfirm'];
const TEXT = ['couponFilter', 'claimKeywords', 'excludeKeywords', 'customSelector', 'couponTabs'];

let schedules = [];
let upcoming = {};

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k in e && typeof v !== 'string') e[k] = v;
    else e.setAttribute(k, v);
  }
  for (const k of kids) e.append(k);
  return e;
}

function flash(node, text, cls = 'ok') {
  node.textContent = text;
  node.className = `small ${cls}`;
  clearTimeout(node._t);
  node._t = setTimeout(() => { node.textContent = ''; }, 3000);
}

// ------------------------------------------------------------ schedules --
function renderSchedules() {
  const body = $('schedList');
  body.replaceChildren();
  $('schedEmpty').hidden = schedules.length > 0;
  for (const s of schedules) {
    const next = upcoming[s.id];
    const nextText = !s.enabled ? '已暫停' : (next ? C.fmtDateTime(next) : (s.mode === 'once' ? '已過期／已執行' : '—'));
    body.append(el('tr', {},
      el('td', {}, el('input', { type: 'checkbox', checked: !!s.enabled, onchange: (e) => update(s.id, { enabled: e.target.checked }) })),
      el('td', {}, s.name || el('span', { class: 'muted' }, '（未命名）')),
      el('td', { class: 'tabular' }, C.describeSchedule(s)),
      el('td', { class: 'tabular small' }, nextText),
      el('td', {}, el('button', { class: 'danger', onclick: () => remove(s.id) }, '刪除')),
    ));
  }
}

async function saveSchedules() {
  await chrome.storage.local.set({ schedules });
}
async function update(id, p) {
  schedules = schedules.map((s) => (s.id === id ? { ...s, ...p } : s));
  await saveSchedules();
}
async function remove(id) {
  schedules = schedules.filter((s) => s.id !== id);
  await saveSchedules();
}

function syncModeFields() {
  const once = $('newMode').value === 'once';
  $('dateField').hidden = !once;
  $('daysField').hidden = once;
}

async function addSchedule() {
  const mode = $('newMode').value;
  let time = $('newTime').value;
  if (/^\d{2}:\d{2}$/.test(time)) time += ':00';
  const s = {
    id: `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`,
    name: $('newName').value.trim(),
    mode,
    time,
    enabled: true,
  };
  if (mode === 'once') s.date = $('newDate').value;
  else s.days = Array.from(document.querySelectorAll('#newDays input:checked')).map((i) => Number(i.value));
  const err = C.validateSchedule(s);
  if (err) return flash($('addMsg'), err, 'err');
  const next = C.nextRun(s, Date.now());
  if (next === null) return flash($('addMsg'), '這個時間已經過了', 'err');
  schedules.push(s);
  await saveSchedules();
  $('newName').value = '';
  flash($('addMsg'), `已新增，下一次 ${C.fmtDateTime(next)}`);
}

// ------------------------------------------------------------- settings --
function fillSettings(s) {
  for (const k of NUM) $(k).value = s[k];
  for (const k of BOOL) $(k).checked = !!s[k];
  for (const k of TEXT) $(k).value = s[k];
}
function readSettings() {
  const s = {};
  for (const k of NUM) s[k] = Number($(k).value);
  for (const k of BOOL) s[k] = $(k).checked;
  for (const k of TEXT) s[k] = $(k).value.trim();
  return C.sanitizeSettings(s);
}
async function saveSettings() {
  const s = readSettings();
  if (s.customSelector) {
    try { document.querySelector(s.customSelector); } catch (e) { return flash($('saveMsg'), 'CSS 選擇器格式不正確', 'err'); }
  }
  await chrome.storage.local.set({ settings: s });
  fillSettings(s);
  flash($('saveMsg'), '已儲存');
}

// ------------------------------------------------------------------ log --
function renderLog(log) {
  const box = $('log');
  box.replaceChildren();
  if (!log || !log.length) { box.append(el('div', { class: 'muted' }, '沒有紀錄')); return; }
  for (const e of log) {
    box.append(el('div', { class: e.level === 'ok' ? 'ok' : (e.level === 'warn' ? 'warn' : '') },
      el('span', { class: 'muted tabular' }, `${C.fmtDateTime(e.t)}  `), e.msg));
  }
}

// ----------------------------------------------------------------- init --
async function load() {
  const d = await chrome.storage.local.get(['schedules', 'upcoming', 'settings', 'log']);
  schedules = Array.isArray(d.schedules) ? d.schedules : [];
  upcoming = d.upcoming || {};
  renderSchedules();
  renderLog(d.log);
  return d;
}

C.WEEKDAYS.forEach((w, i) => {
  $('newDays').append(el('label', {}, el('input', { type: 'checkbox', value: String(i) }), w));
});
const today = new Date();
$('newDate').value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
$('newMode').addEventListener('change', syncModeFields);
$('addSched').addEventListener('click', addSchedule);
$('saveSettings').addEventListener('click', saveSettings);
$('resetSettings').addEventListener('click', () => fillSettings(C.sanitizeSettings(null)));
$('testNow').addEventListener('click', () => chrome.runtime.sendMessage({ type: 'startNow', delayMs: 5000 }));
$('clearLog').addEventListener('click', () => chrome.storage.local.set({ log: [] }));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.schedules || changes.upcoming) {
    if (changes.schedules) schedules = changes.schedules.newValue || [];
    if (changes.upcoming) upcoming = changes.upcoming.newValue || {};
    renderSchedules();
  }
  if (changes.log) renderLog(changes.log.newValue);
});
syncModeFields();
load().then((d) => fillSettings(C.sanitizeSettings(d.settings)));
