import { getTargets } from './lib/store.js';
import { el, formatTime } from './lib/ui.js';

const list = document.getElementById('list');
const lastRunEl = document.getElementById('lastRun');
const checkBtn = document.getElementById('checkNow');

async function render() {
  const [targets, { lastRun }] = await Promise.all([getTargets(), chrome.storage.local.get('lastRun')]);
  const results = (lastRun && lastRun.results) || {};
  lastRunEl.textContent = lastRun ? `上次檢查：${formatTime(lastRun.at)}` : '尚未檢查';

  list.replaceChildren();
  if (!targets.length) {
    list.append(el('div', { class: 'empty muted' }, '還沒有監測任何商品。', el('br'),
      el('button', { class: 'link', onclick: () => chrome.runtime.openOptionsPage() }, '新增商品')));
    return;
  }

  for (const t of targets) {
    const r = results[t.id];
    const opts = el('div', { class: 'opts' });
    if (t.enabled === false) {
      opts.append(el('span', { class: 'chip out' }, '已暫停'));
    } else if (!r) {
      opts.append(el('span', { class: 'muted small' }, '等待第一次檢查'));
    } else if (!r.ok) {
      opts.append(el('span', { class: 'chip err', title: r.error }, '讀取失敗'),
        el('span', { class: 'err small' }, r.error));
    } else {
      const watched = r.options.filter(([, , w]) => w);
      for (const [name, soldOut] of watched) {
        opts.append(el('span', { class: `chip ${soldOut ? 'out' : 'ok'}` }, `${name}・${soldOut ? '售完' : '有貨'}`));
      }
      if (!watched.length) opts.append(el('span', { class: 'warn small' }, '指定的選項都不在頁面上'));
    }
    list.append(el('div', { class: 'card item' },
      el('div', { class: 'row' },
        el('a', { class: 'name grow ellipsis', href: t.url, target: '_blank', title: t.url }, t.label)),
      opts));
  }
}

checkBtn.addEventListener('click', async () => {
  checkBtn.disabled = true;
  checkBtn.textContent = '檢查中…';
  try {
    await chrome.runtime.sendMessage({ type: 'checkNow' });
  } finally {
    checkBtn.disabled = false;
    checkBtn.textContent = '立即檢查';
    render();
  }
});

document.getElementById('openOptions').addEventListener('click', () => chrome.runtime.openOptionsPage());

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && (changes.lastRun || changes.targets)) render();
});

render();
