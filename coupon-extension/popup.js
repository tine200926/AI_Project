const C = globalThis.CouponCore;
const $ = (id) => document.getElementById(id);
let state = {};

async function load() {
  state = await chrome.storage.local.get(['run', 'upcoming', 'schedules']);
  render();
}

function render() {
  const now = Date.now();
  const run = state.run && state.run.end > now ? state.run : null;
  $('status').classList.toggle('active', !!run);
  $('stop').disabled = !run;
  if (run) {
    const before = run.target - now;
    $('statusTitle').textContent = `執行中：${run.label}`;
    $('big').textContent = before > 0 ? C.fmtCountdown(before + 999) : `已點擊 ${run.clicks} 次`;
    $('sub').textContent = before > 0 ? `目標 ${C.fmtDateTime(run.target, true)}` : `剩 ${Math.ceil((run.end - now) / 1000)} 秒`;
    return;
  }
  const schedules = state.schedules || [];
  const upcoming = Object.entries(state.upcoming || {})
    .map(([id, t]) => ({ s: schedules.find((x) => x.id === id), t }))
    .filter((x) => x.s && x.s.enabled && x.t > now - 60000)
    .sort((a, b) => a.t - b.t);
  $('statusTitle').textContent = '下一次領取';
  if (!upcoming.length) {
    $('big').textContent = '沒有排程';
    $('sub').textContent = '到「排程與設定」新增時間';
    return;
  }
  const next = upcoming[0];
  $('big').textContent = C.fmtCountdown(next.t - now + 999);
  $('sub').textContent = `${next.s.name ? next.s.name + '｜' : ''}${C.fmtDateTime(next.t)}`;
}

$('startNow').addEventListener('click', async () => {
  await chrome.runtime.sendMessage({ type: 'startNow', delayMs: 5000 });
  window.close();
});
$('stop').addEventListener('click', () => chrome.runtime.sendMessage({ type: 'stop' }));
$('openPage').addEventListener('click', () => chrome.tabs.create({ url: C.PAGE_URL }));
$('openOptions').addEventListener('click', () => chrome.runtime.openOptionsPage());
chrome.storage.onChanged.addListener((changes, area) => { if (area === 'local') load(); });
setInterval(render, 250);
load();
