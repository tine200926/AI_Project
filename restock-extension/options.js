import {
  isShopUrl, normalizeUrl, resolveWatched, splitWatch, uniqueTargetId,
} from './lib/core.js';
import {
  DEFAULT_SETTINGS, dropTargetState, getSettings, getTargets, log, saveSettings, saveTargets,
} from './lib/store.js';
import { el, formatTime } from './lib/ui.js';

const $ = (id) => document.getElementById(id);

function toast(text) {
  const t = $('toast');
  t.textContent = text;
  t.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove('show'), 2400);
}

async function send(msg) {
  const resp = await chrome.runtime.sendMessage(msg);
  if (!resp || !resp.ok) throw new Error((resp && resp.error) || '沒有回應');
  return resp;
}

function checkSoon() {
  send({ type: 'checkNow' }).catch((e) => toast(`檢查失敗：${e.message}`));
}

// ------------------------------------------------------------- add flow ----
$('probeForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const out = $('probeResult');
  const url = normalizeUrl($('url').value);
  const country = $('country').value.trim().toUpperCase();
  if (!isShopUrl(url)) {
    out.replaceChildren(el('div', { class: 'err' }, '網址要是 https://shop.weverse.io/ 開頭的商品頁。'));
    return;
  }
  $('probeBtn').disabled = true;
  out.replaceChildren(el('div', { class: 'muted' }, '讀取中…'));
  try {
    const probe = await send({ type: 'probe', url, country });
    renderProbe(probe, country);
  } catch (e) {
    out.replaceChildren(el('div', { class: 'err' }, `讀不到這個頁面：${e.message}`));
  } finally {
    $('probeBtn').disabled = false;
  }
});

function renderProbe(probe, country) {
  const out = $('probeResult');
  const single = probe.options.length === 1;
  const all = el('input', { type: 'checkbox', checked: true });
  const boxes = probe.options.map(([name, soldOut]) => {
    const cb = el('input', { type: 'checkbox', value: name, disabled: true });
    return {
      cb,
      node: el('label', {}, cb, name,
        el('span', { class: `chip ${soldOut ? 'out' : 'ok'}` }, soldOut ? '售完' : '有貨')),
    };
  });
  all.addEventListener('change', () => boxes.forEach((b) => { b.cb.disabled = all.checked; }));
  const label = el('input', { type: 'text', value: probe.saleName || probe.url });

  const add = el('button', { class: 'primary' }, '加入監測');
  add.addEventListener('click', async () => {
    const picked = boxes.filter((b) => b.cb.checked).map((b) => b.cb.value);
    if (!all.checked && !single && !picked.length) { toast('請至少勾選一個選項，或勾「全部選項」'); return; }
    const watch = all.checked || single ? ['*'] : picked;
    await addTarget({ url: probe.url, label: label.value.trim() || probe.url, watch, country });
    out.replaceChildren();
    $('url').value = '';
    $('country').value = '';
  });

  out.replaceChildren(
    el('label', { class: 'field' }, el('span', {}, '名稱'), label),
    single
      ? el('div', { class: 'muted small' }, '這個商品只有單一規格，會直接監測。')
      : el('div', { style: 'display:grid;gap:6px' },
        el('label', { class: 'check' }, all, el('span', {}, el('b', {}, '全部選項'), '（包含之後才出現的選項）')),
        el('div', { class: 'optlist' }, boxes.map((b) => b.node))),
    el('div', {}, add),
  );
}

async function addTarget({ url, label, watch, country }) {
  const targets = await getTargets();
  const existing = targets.find((t) => t.url === url);
  if (existing) {
    existing.watch = watch;
    existing.enabled = true;
    if (country) existing.country = country;
    await saveTargets(targets);
    toast('這個商品已經在清單裡，已更新監測選項');
  } else {
    const id = uniqueTargetId(url, targets.map((t) => t.id));
    targets.push({ id, label, url, watch, country: country || '', enabled: true });
    await saveTargets(targets);
    log(`新增監測：${label}（${watch.join(', ')}）`);
    toast('已加入監測');
  }
  checkSoon();
}

// --------------------------------------------------------------- list ----
async function renderTargets() {
  const [targets, { lastRun }] = await Promise.all([getTargets(), chrome.storage.local.get('lastRun')]);
  const results = (lastRun && lastRun.results) || {};
  $('lastRun').textContent = lastRun ? `上次檢查：${formatTime(lastRun.at)}` : '';
  const box = $('targets');
  box.replaceChildren();
  if (!targets.length) {
    box.append(el('div', { class: 'muted' }, '還沒有任何商品，從上方「新增商品」加入。'));
    return;
  }
  for (const t of targets) box.append(renderTarget(t, results[t.id]));
}

async function updateTarget(id, patch) {
  const targets = await getTargets();
  const t = targets.find((x) => x.id === id);
  if (!t) return;
  Object.assign(t, patch);
  await saveTargets(targets);
}

function renderTarget(t, result) {
  const enabled = el('input', {
    type: 'checkbox',
    checked: t.enabled !== false,
    title: '取消勾選 = 暫停監測',
    onchange: async (e) => {
      await updateTarget(t.id, { enabled: e.target.checked });
      toast(e.target.checked ? '已恢復監測' : '已暫停');
    },
  });

  const watchText = (t.watch || ['*']).join(', ');
  const watchInput = el('input', { type: 'text', value: watchText, title: '用逗號分隔；* = 全部選項' });
  const saveWatch = async (list) => {
    const watch = list.length ? list : ['*'];
    await updateTarget(t.id, { watch });
    toast('已更新監測選項');
  };
  watchInput.addEventListener('change', () => saveWatch(splitWatch(watchInput.value)));

  // Every option seen on the last check, as clickable chips that toggle it in
  // the watch list - easier than typing exact names.
  const opts = el('div', { class: 'opts' });
  if (result && result.ok) {
    const current = t.watch || ['*'];
    for (const [name, soldOut, watched] of result.options) {
      opts.append(el('button', {
        class: `chip ${watched ? (soldOut ? 'out' : 'ok') : ''}`,
        style: watched ? '' : 'border:1px dashed var(--border);background:none;color:var(--muted)',
        title: watched ? '點一下停止監測這個選項' : '點一下加入監測',
        onclick: () => {
          let next;
          if (watched) {
            const base = current.includes('*') ? result.options.map(([n]) => n) : current;
            // Drop every watch entry that resolves to this option.
            next = base.filter((w) => !resolveWatched([w], [name]).resolved.length);
          } else {
            next = current.includes('*') ? current : [...current, name];
          }
          saveWatch(next);
        },
      }, `${name}・${soldOut ? '售完' : '有貨'}`));
    }
    if (result.missing && result.missing.length) {
      opts.append(el('span', { class: 'warn small' }, `頁面上找不到：${result.missing.join(', ')}`));
    }
  } else if (result && !result.ok) {
    opts.append(el('span', { class: 'err small' }, result.error));
  } else {
    opts.append(el('span', { class: 'muted small' }, '等待第一次檢查'));
  }

  const del = el('button', {
    class: 'danger',
    onclick: async () => {
      if (!confirm(`要移除「${t.label}」嗎？`)) return;
      const targets = (await getTargets()).filter((x) => x.id !== t.id);
      await saveTargets(targets);
      await dropTargetState(t.id);
      log(`移除監測：${t.label}`);
      toast('已移除');
    },
  }, '移除');

  const rename = el('button', {
    class: 'link small',
    onclick: async () => {
      const name = prompt('新的名稱', t.label);
      if (name && name.trim()) await updateTarget(t.id, { label: name.trim() });
    },
  }, '改名');

  return el('div', { class: `target${t.enabled === false ? ' paused' : ''}` },
    el('div', { class: 'row' }, enabled,
      el('a', { class: 'tlabel grow ellipsis', href: t.url, target: '_blank', title: t.url }, t.label),
      rename, del),
    el('label', { class: 'field' },
      el('span', {}, '監測選項（逗號分隔，* = 全部；也可以點下方的選項切換）'), watchInput),
    opts,
    t.country ? el('div', { class: 'muted small' }, `國家：${t.country}`) : null);
}

// ------------------------------------------------------------ settings ----
async function renderSettings() {
  const s = await getSettings();
  const form = $('settingsForm');
  for (const k of Object.keys(DEFAULT_SETTINGS)) {
    const input = form.elements[k];
    if (!input) continue;
    if (input.type === 'checkbox') input.checked = !!s[k];
    else input.value = s[k];
  }
}

$('settingsForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  const current = await getSettings();
  const next = { ...current };
  for (const k of Object.keys(DEFAULT_SETTINGS)) {
    const input = form.elements[k];
    if (!input) continue;
    next[k] = input.type === 'checkbox' ? input.checked : input.value;
  }
  await saveSettings(next);
  await renderSettings();
  toast('設定已儲存');
});

// ------------------------------------------------------- import/export ----
$('exportBtn').addEventListener('click', async () => {
  const [targets, settings] = await Promise.all([getTargets(), getSettings()]);
  const data = {
    targets: targets.map(({ label, url, watch, country, enabled }) => (
      { label, url, watch, ...(country ? { country } : {}), ...(enabled === false ? { enabled } : {}) })),
    settings,
  };
  $('io').value = JSON.stringify(data, null, 2);
  $('io').select();
});

$('importBtn').addEventListener('click', async () => {
  let data;
  try {
    data = JSON.parse($('io').value);
  } catch (e) {
    toast(`不是有效的 JSON：${e.message}`);
    return;
  }
  // Accept: {targets:[...]}, a bare array, or the old single-product shape.
  let rows = Array.isArray(data) ? data
    : Array.isArray(data && data.targets) ? data.targets
      : data && data.url ? [data] : [];
  rows = rows.filter((r) => r && typeof r.url === 'string');
  if (!rows.length) { toast('找不到任何商品（需要 targets 陣列，每項要有 url）'); return; }

  const targets = await getTargets();
  let added = 0;
  let skipped = 0;
  for (const r of rows) {
    const url = normalizeUrl(r.url);
    if (!isShopUrl(url) || targets.some((t) => t.url === url)) { skipped++; continue; }
    const watch = Array.isArray(r.watch) ? r.watch.map(String).filter(Boolean) : splitWatch(r.watch);
    targets.push({
      id: uniqueTargetId(url, targets.map((t) => t.id)),
      label: (r.label && String(r.label).trim()) || url,
      url,
      watch: watch.length ? watch : ['*'],
      country: r.country ? String(r.country).trim().toUpperCase() : '',
      enabled: r.enabled !== false,
    });
    added++;
  }
  await saveTargets(targets);
  if (data && data.settings && typeof data.settings === 'object' && !Array.isArray(data.settings)) {
    await saveSettings({ ...(await getSettings()), ...data.settings });
    await renderSettings();
  }
  log(`匯入：新增 ${added} 個商品，略過 ${skipped} 個`);
  toast(`新增 ${added} 個，略過 ${skipped} 個（重複或網址不對）`);
  if (added) checkSoon();
});

// ---------------------------------------------------------------- log ----
async function renderLog() {
  const { log: entries } = await chrome.storage.local.get('log');
  const list = (Array.isArray(entries) ? entries : []).slice(-150).reverse();
  $('log').replaceChildren(...(list.length
    ? list.map((e) => el('div', { class: e.level === 'ERROR' ? 'err' : e.level === 'WARN' ? 'warn' : '' },
      `${new Date(e.t).toLocaleString('zh-TW', { hour12: false })}  ${e.msg}`))
    : [el('span', { class: 'muted' }, '還沒有紀錄')]));
}

$('clearLog').addEventListener('click', async () => {
  await chrome.storage.local.set({ log: [] });
});

// ------------------------------------------------------------- header ----
$('checkNow').addEventListener('click', async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  btn.textContent = '檢查中…';
  try {
    await send({ type: 'checkNow' });
    toast('檢查完成');
  } catch (e) {
    toast(`檢查失敗：${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = '立即檢查';
  }
});

$('testNotify').addEventListener('click', () => {
  send({ type: 'testNotify' })
    .then(() => toast('已送出測試通知；沒看到的話，請檢查系統的通知設定'))
    .catch((e) => toast(e.message));
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.targets || changes.lastRun) renderTargets();
  if (changes.log) renderLog();
});

renderTargets();
renderSettings();
renderLog();
