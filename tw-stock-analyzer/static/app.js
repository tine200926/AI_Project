"use strict";
// 台股慣例：紅色 = 上漲／偏多，綠色 = 下跌／偏空
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toLocaleString("zh-TW", { maximumFractionDigits: d, minimumFractionDigits: d }));
const pct = (v, d = 1) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${num(v, d)}%`);
const signCls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
const toneCls = (label) => (label === "偏多" ? "up" : label === "偏空" ? "down" : "muted");
const money = (v) => (v === null || v === undefined ? "—" : v >= 1e8 ? `${num(v / 1e8, 2)} 億` : v >= 1e4 ? `${num(v / 1e4, 0)} 萬` : num(v, 0));
const lvl = (l) => `<span class="badge lvl-${esc(l)}">風險 ${esc(l)}</span>`;
const state = { overview: null, charts: [], chat: [] };

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : `HTTP ${res.status}`);
  return body;
}
function loading(el, text = "計算中…") { el.innerHTML = `<div class="card muted"><span class="spinner"></span>${esc(text)}</div>`; }
function fail(el, e) { el.innerHTML = `<div class="card err">⚠ ${esc(e.message || e)}</div>`; }
async function withBtn(btn, fn) { btn.disabled = true; try { await fn(); } finally { btn.disabled = false; } }
function src(meta) {
  if (!meta) return "";
  if (meta.error) return `<div class="src">資料來源：取得失敗（${esc(meta.error)}）</div>`;
  const notes = (meta.notes || []).map((n) => `<div>※ ${esc(n)}</div>`).join("");
  return `<div class="src">資料來源：${esc(meta.source)}${meta.as_of ? `｜資料日期 ${esc(meta.as_of)}` : ""}${meta.is_demo ? "｜<b>模擬資料</b>" : ""}${notes}</div>`;
}
function clearCharts() { state.charts.forEach((c) => c.remove()); state.charts = []; }
function themeColors() {
  const cs = getComputedStyle(document.documentElement);
  const g = (n) => cs.getPropertyValue(n).trim();
  return { text: g("--text"), line: g("--line"), up: g("--up"), down: g("--down"), accent: g("--accent"), muted: g("--muted"), bg: g("--card") };
}
const noopSeries = { setData() {} };
const noopChart = { addCandlestickSeries: () => noopSeries, addHistogramSeries: () => noopSeries, addLineSeries: () => noopSeries,
  priceScale: () => ({ applyOptions() {} }), timeScale: () => ({ fitContent() {} }), remove() {} };
function makeChart(el, height) {
  if (typeof LightweightCharts === "undefined") {
    el.innerHTML = `<div class="muted small">圖表元件載入失敗（static/vendor/lightweight-charts.standalone.production.js）。</div>`;
    el.style.height = "auto";
    return noopChart;
  }
  const c = themeColors();
  const chart = LightweightCharts.createChart(el, {
    height, layout: { background: { color: c.bg }, textColor: c.text },
    grid: { vertLines: { color: c.line }, horzLines: { color: c.line } },
    rightPriceScale: { borderColor: c.line }, timeScale: { borderColor: c.line },
  });
  state.charts.push(chart);
  new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth })).observe(el);
  return chart;
}

// ---------------------------------------------------------------- 分頁
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${name}`));
}
$("#quick").addEventListener("submit", (e) => { e.preventDefault(); const c = $("#quick-code").value.trim(); if (c) { $("#stock-code").value = c; showTab("stock"); runStock(); } });

// ---------------------------------------------------------------- 總覽
async function loadOverview() {
  try {
    const o = await api("/api/overview");
    state.overview = o;
    $("#demo-banner").hidden = o.mode !== "demo";
    for (const [k, p] of Object.entries(o.presets)) {
      $("#preset").insertAdjacentHTML("beforeend", `<option value="${k}">${esc(p.name)}</option>`);
      $("#bt-preset").insertAdjacentHTML("beforeend", `<option value="${k}">${esc(p.name)}</option>`);
    }
    $("#bt-preset").value = o.regime.preset || "momentum";
    for (const [k, s] of Object.entries(o.strategies)) $("#sb-strategy").insertAdjacentHTML("beforeend", `<option value="${k}">${esc(s.name)}</option>`);
    const bt = o.benchmark?.technical || {};
    const regimeName = { bull: "多頭", bear: "空頭", range: "盤整", unknown: "未知" }[o.regime.regime];
    $("#regime-card").innerHTML = `
      <div class="row wrap gap" style="align-items:center;justify-content:space-between">
        <div><div class="muted small">大盤狀態（以 0050 代理）</div>
          <div class="score-big ${o.regime.regime === "bull" ? "up" : o.regime.regime === "bear" ? "down" : ""}">${esc(regimeName)}</div></div>
        <div class="grow"><div>${esc(o.regime.reason)}</div>
          <div class="muted small">目前市場主流策略：<b>${esc(o.presets[o.regime.preset]?.name || "—")}</b>；0050 技術面 <span class="${toneCls(bt.label)}">${esc(bt.label || "—")}（${num(bt.score, 0)}）</span></div></div>
      </div>${src(o.benchmark)}`;
    describePreset();
    runScreen();
  } catch (e) { fail($("#regime-card"), e); }
}
function describePreset() {
  const k = $("#preset").value, o = state.overview;
  if (!o) return;
  const key = k === "auto" ? o.regime.preset : k;
  const p = o.presets[key];
  const w = Object.entries(p.weights).map(([f, v]) => `${o.factors[f]} ${Math.round(v * 100)}%`).join("、");
  $("#preset-desc").textContent = `${k === "auto" ? "（自動）" : ""}${p.name}：${p.desc} 因子權重：${w}`;
}
$("#preset").addEventListener("change", describePreset);
$("#run-screen").addEventListener("click", (e) => withBtn(e.target, runScreen));

async function runScreen() {
  const out = $("#screen-out");
  loading(out, "抓取價格與財務資料、計算因子排名（第一次約需 30 秒）…");
  const q = new URLSearchParams({ preset: $("#preset").value, top_n: $("#topn").value });
  if ($("#universe").value.trim()) q.set("universe", $("#universe").value.trim());
  try {
    const r = await api(`/api/screen?${q}`);
    const o = state.overview;
    const rows = r.picks.map((p, i) => `
      <tr><td>${i + 1}</td>
        <td><a href="#" data-code="${esc(p.code)}" class="go">${esc(p.code)} ${esc(p.name)}</a><div class="muted small">${esc(p.industry)}</div></td>
        <td class="r"><b>${num(p.score, 1)}</b><div class="bar"><span style="width:${p.score}%"></span></div></td>
        <td class="r">${num(p.close)}</td>
        <td><span class="${toneCls(p.technical.label)}">${esc(p.technical.label || "—")}</span></td>
        <td>${lvl(p.risk.level)}<div class="small muted">停損 ${num(p.risk.stop_loss)}（${pct(p.risk.stop_loss_pct)}）</div>
          ${p.risk.flags.filter((f) => f.level !== "info").map((f) => `<div class="small lvl-${f.level === "high" ? "高" : "中"}">• ${esc(f.text)}</div>`).join("")}</td>
        <td class="small"><ul class="plain">${p.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
          ${p.weaknesses.length ? `<div class="muted">弱項：${p.weaknesses.map(esc).join("；")}</div>` : ""}</td></tr>`).join("");
    out.innerHTML = `
      <div class="card"><h3>${esc(r.preset_name)}｜前 ${r.picks.length} 名 <span class="muted small">（${r.as_of}，${r.eligible}/${r.universe_size} 檔通過風險過濾）</span></h3>
        <div class="table-wrap"><table><thead><tr><th>#</th><th>股票</th><th class="r">綜合分數</th><th class="r">收盤</th><th>技術面</th><th>風險控管</th><th>入選理由（實際數據）</th></tr></thead><tbody>${rows}</tbody></table></div>
        ${r.factors_dropped.length ? `<p class="small muted">本次缺資料而剔除的因子：${r.factors_dropped.map((f) => esc(o.factors[f])).join("、")}（權重已重新分配）</p>` : ""}
        ${r.notes.map((n) => `<p class="small muted">※ ${esc(n)}</p>`).join("")}
        <div class="src">價格：${esc(r.sources.prices.join("、"))}${r.sources.snapshot ? `｜估值／營收：${esc(r.sources.snapshot.source)}（${esc(r.sources.snapshot.as_of || "")}）` : ""}</div>
      </div>
      ${r.excluded.length ? `<div class="card"><h3>風險過濾排除</h3><ul class="plain small">${r.excluded.map((x) => `<li>${esc(x.code)} ${esc(x.name)}：${esc(x.reason)}</li>`).join("")}</ul></div>` : ""}`;
    out.querySelectorAll("a.go").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); $("#stock-code").value = a.dataset.code; showTab("stock"); runStock(); }));
  } catch (e) { fail(out, e); }
}

// ---------------------------------------------------------------- 個股
$("#run-stock").addEventListener("click", (e) => withBtn(e.target, runStock));
async function runStock() {
  const out = $("#stock-out");
  const code = $("#stock-code").value.trim();
  if (!code) return;
  loading(out, `分析 ${code}…`);
  const q = new URLSearchParams({ capital: $("#capital").value, risk_pct: $("#risk-pct").value, atr_mult: $("#atr-mult").value });
  try {
    const s = await api(`/api/stock/${encodeURIComponent(code)}?${q}`);
    clearCharts();
    const t = s.technical, f = s.fundamentals, rk = s.risk, se = s.sentiment, v = s.verdict;
    const L = t.latest || {};
    out.innerHTML = `
      <div class="card">
        <div class="row wrap gap" style="align-items:center;justify-content:space-between">
          <div><div class="score-big">${esc(s.code)} ${esc(s.name)}</div><div class="muted">${esc(s.industry)}｜${esc(L.date || "")}</div></div>
          <div><div class="score-big ${signCls(L.change_pct)}">${num(L.close)}</div><div class="${signCls(L.change_pct)}">${pct(L.change_pct, 2)}</div></div>
          <div><div class="muted small">綜合判讀</div><div class="score-big ${toneCls(v.label)}">${esc(v.label)} ${v.score !== null ? num(v.score, 0) : ""}</div>
            <div class="small muted">${v.parts.map((p) => `${esc(p.name)} ${num(p.score, 0)}（權重 ${p.weight * 100}%）`).join("、")}</div></div>
          <div>${lvl(rk.level)}</div>
        </div>
        ${v.caution ? `<p class="lvl-高 small">⚠ ${esc(v.caution)}</p>` : ""}
        <div id="kchart" class="chart"></div><div id="ichart" class="chart sm"></div>
        <div class="small muted">K 線 + MA20（藍）/ MA60（橙）/ MA240（灰）；下方為 KD。</div>${src(s.price_source)}
      </div>
      <div class="grid2">
        <div class="card"><h3>技術面訊號 <span class="${toneCls(t.label)}">${esc(t.label || "")} ${num(t.score, 0)}</span></h3>
          ${t.error ? `<div class="err">${esc(t.error)}</div>` : `
          <table><tbody>${t.signals.map((x) => `<tr><td class="${x.direction > 0 ? "up" : "down"}">${x.direction > 0 ? "▲" : "▼"} ${esc(x.name)}</td><td class="small muted">${esc(x.detail)}</td><td class="r small">${x.weight}</td></tr>`).join("")}</tbody></table>
          <div class="kpis" style="margin-top:10px">
            ${[["RSI14", L.rsi14, 1], ["K / D", null], ["MACD 柱", L.hist, 2], ["量比", L.volume_ratio, 2], ["支撐(60日低)", t.support, 2], ["壓力(60日高)", t.resistance, 2]].map(([k, val, d]) =>
              `<div class="kpi"><div class="k">${k}</div><div class="v">${k === "K / D" ? `${num(L.k, 0)} / ${num(L.d, 0)}` : num(val, d)}</div></div>`).join("")}
          </div>`}</div>
        <div class="card"><h3>風險控管 ${lvl(rk.level)}</h3>
          <ul class="plain small">${rk.flags.map((x) => `<li class="${x.level === "high" ? "lvl-高" : x.level === "mid" ? "lvl-中" : "muted"}">${esc(x.text)}</li>`).join("") || "<li class='muted'>無特別風險旗標</li>"}</ul>
          <div class="kpis">
            <div class="kpi"><div class="k">60日年化波動</div><div class="v">${num(rk.metrics.volatility_60d_ann_pct, 1)}%</div></div>
            <div class="kpi"><div class="k">近一年最大回撤</div><div class="v">${num(rk.metrics.max_drawdown_1y_pct, 1)}%</div></div>
            <div class="kpi"><div class="k">Beta(對0050)</div><div class="v">${num(rk.metrics.beta_1y)}</div></div>
            <div class="kpi"><div class="k">20日均成交值</div><div class="v">${money(rk.metrics.avg_turnover_20d)}</div></div>
          </div>
          <h4>交易計畫（參考）</h4>
          <table><tbody>
            <tr><td>參考進場</td><td class="r">${num(rk.plan.entry_ref)}</td></tr>
            <tr><td>停損（${rk.plan.atr_mult}×ATR）</td><td class="r down">${num(rk.plan.stop_loss)}（${pct(rk.plan.stop_loss_pct)}）</td></tr>
            <tr><td>2R 目標價</td><td class="r up">${num(rk.plan.take_profit_2r)}</td></tr>
            <tr><td>建議股數（資金 ${money(rk.plan.capital)}，單筆風險 ${rk.plan.risk_pct}%）</td><td class="r"><b>${rk.plan.sizing.lots} 張 ${rk.plan.sizing.odd_shares} 股</b><div class="small muted">約 ${money(rk.plan.sizing.position_value)}，佔 ${num(rk.plan.sizing.weight_pct, 1)}%，停損最大虧損 ${money(rk.plan.sizing.max_loss)}（受限於${esc(rk.plan.sizing.binding || "")}）</div></td></tr>
            <tr><td colspan="2" class="small muted">移動停損：${esc(rk.plan.trailing_rule)}</td></tr>
          </tbody></table></div>
      </div>
      <div class="grid2">
        <div class="card"><h3>基本面與財報摘要</h3>
          <ul class="plain">${f.highlights.map((h) => `<li class="tone-${h.tone}">${esc(h.text)}</li>`).join("") || "<li class='muted'>無資料</li>"}</ul>
          ${f.missing.length ? `<p class="small muted">缺少：${f.missing.map(esc).join("、")}</p>` : ""}
          ${f.financials.quarters?.length ? `<h4>近八季</h4><div class="table-wrap"><table><thead><tr><th>季度</th><th class="r">營收</th><th class="r">毛利率</th><th class="r">EPS</th></tr></thead><tbody>
            ${f.financials.quarters.map((q) => `<tr><td>${esc(q.period)}</td><td class="r">${money(q.revenue)}</td><td class="r">${num(q.gross_margin, 1)}%</td><td class="r">${num(q.eps)}</td></tr>`).join("")}</tbody></table></div>` : ""}
          ${f.revenue.series?.length ? `<h4>月營收年增率</h4><div id="revchart" class="chart sm"></div>` : ""}
          ${src(f.sources.financials)}${src(f.sources.revenue)}${src(f.sources.valuation)}</div>
        <div class="card"><h3>新聞輿情 ${se.error ? "" : `<span class="${toneCls(se.label)}">${esc(se.label)} ${num(se.score, 0)}</span> <span class="small muted">可信度 ${esc(se.confidence)}</span>`}</h3>
          ${se.error ? `<div class="err">${esc(se.error)}</div>` : newsBlock(se, 8)}
          ${s.institutional && !s.institutional.error ? `<h4>三大法人買賣超（股）</h4><table><thead><tr><th></th><th class="r">外資</th><th class="r">投信</th><th class="r">自營</th><th class="r">合計</th></tr></thead><tbody>
            ${[["近5日", s.institutional.sum_5d], ["近20日", s.institutional.sum_20d]].map(([n, d]) => `<tr><td>${n}</td>${["foreign", "trust", "dealer", "total"].map((k) => `<td class="r ${signCls(d[k])}">${num(d[k], 0)}</td>`).join("")}</tr>`).join("")}
          </tbody></table>${src(s.institutional.source)}` : ""}</div>
      </div>`;
    drawStockCharts(s.chart);
    if (f.revenue.series?.length) {
      const ch = makeChart($("#revchart"), 140);
      const c = themeColors();
      ch.addHistogramSeries({ priceFormat: { type: "percent" } }).setData(f.revenue.series.filter((x) => x.yoy !== null)
        .map((x) => ({ time: `${x.month}-01`, value: x.yoy, color: x.yoy >= 0 ? c.up : c.down })));
      ch.timeScale().fitContent();
    }
  } catch (e) { fail(out, e); }
}
function drawStockCharts(d) {
  const c = themeColors();
  const k = makeChart($("#kchart"), 360);
  k.addCandlestickSeries({ upColor: c.up, downColor: c.down, borderUpColor: c.up, borderDownColor: c.down, wickUpColor: c.up, wickDownColor: c.down }).setData(d.candles);
  const vol = k.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" } });
  k.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
  vol.setData(d.volume.map((v, i) => ({ ...v, color: d.candles[i].close >= d.candles[i].open ? c.up + "66" : c.down + "66" })));
  k.addLineSeries({ color: c.accent, lineWidth: 1 }).setData(d.ma20);
  k.addLineSeries({ color: "#e08a00", lineWidth: 1 }).setData(d.ma60);
  k.addLineSeries({ color: c.muted, lineWidth: 1 }).setData(d.ma240);
  k.timeScale().fitContent();
  const ind = makeChart($("#ichart"), 140);
  ind.addLineSeries({ color: c.accent, lineWidth: 1 }).setData(d.k);
  ind.addLineSeries({ color: "#e08a00", lineWidth: 1 }).setData(d.d);
  ind.timeScale().fitContent();
}
function newsBlock(se, n) {
  return `
    <div class="small muted">共 ${se.counts.total} 則，保留 ${se.counts.kept} 則（利多 ${se.counts.positive}／利空 ${se.counts.negative}），排除 ${se.counts.filtered} 則</div>
    ${se.top_tags.length ? `<div class="chips">${se.top_tags.map(([t, c]) => `<span class="chip">${esc(t)} ×${c}</span>`).join("")}</div>` : ""}
    <h4>快速解讀（規則式，只引用標題）</h4><ul class="plain small">${se.digest.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
    <h4>新聞（依影響力排序）</h4>
    <ul class="plain small">${se.articles.slice(0, n).map((a) => `<li><span class="tone-${a.tone}">${a.tone === "pos" ? "▲" : a.tone === "neg" ? "▼" : "•"}</span>
      ${a.link ? `<a href="${esc(a.link)}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a>` : esc(a.title)}
      <span class="muted">｜${esc(a.source)}${a.coverage > 1 ? `｜${a.coverage} 家報導` : ""}${a.hits.length ? `｜${esc(a.hits.slice(0, 4).join(" "))}` : ""}</span></li>`).join("")}</ul>
    ${se.filtered.length ? `<details><summary>已排除 ${se.filtered.length} 則（灌水／重複）</summary><ul class="plain small">${se.filtered.map((x) => `<li>${esc(x.title)} <span class="muted">｜${esc(x.source)}｜${esc(x.reason)}</span></li>`).join("")}</ul></details>` : ""}
    ${src(se.source)}`;
}

// ---------------------------------------------------------------- 新聞
$("#run-news").addEventListener("click", (e) => withBtn(e.target, runNews));
async function runNews() {
  const out = $("#news-out");
  loading(out, "讀取並過濾新聞…");
  try {
    const r = await api(`/api/news/market?days=${$("#news-days").value}&ai=${$("#news-ai").checked}`);
    out.innerHTML = `
      ${r.ai ? `<div class="card"><h3>AI 快速解讀</h3>${r.ai.error ? `<div class="err">${esc(r.ai.error)}</div>` : `<div>${md(r.ai.digest)}</div><div class="src">模型：${esc(r.ai.model)}｜只根據下方過濾後的標題產生</div>`}</div>` : ""}
      <div class="card"><h3>市場輿情 <span class="${toneCls(r.label)}">${esc(r.label)} ${num(r.score, 0)}</span> <span class="small muted">可信度 ${esc(r.confidence)}</span></h3>${newsBlock(r, 40)}</div>`;
  } catch (e) { fail(out, e); }
}

// ---------------------------------------------------------------- 回測
$("#run-bt").addEventListener("click", (e) => withBtn(e.target, async () => {
  const out = $("#bt-out");
  loading(out, "下載歷史資料並回測（第一次可能需要 1 分鐘）…");
  try {
    const r = await api("/api/backtest/factor", { method: "POST", body: JSON.stringify({
      preset: $("#bt-preset").value, years: +$("#bt-years").value, top_n: +$("#bt-topn").value,
      fee_discount: +$("#bt-disc").value, use_valuation: $("#bt-val").checked,
      universe: $("#universe").value.trim() || null }) });
    renderBacktest(out, r, "factor");
  } catch (e) { fail(out, e); }
}));
$("#run-sb").addEventListener("click", (e) => withBtn(e.target, async () => {
  const out = $("#bt-out");
  loading(out, "回測中…");
  try {
    const r = await api("/api/backtest/strategy", { method: "POST", body: JSON.stringify({
      code: $("#sb-code").value.trim(), strategy: $("#sb-strategy").value, years: +$("#sb-years").value,
      stop_atr: +$("#sb-stop").value || null }) });
    renderBacktest(out, r, "strategy");
  } catch (e) { fail(out, e); }
}));
function metricRow(name, m) {
  if (!m) return "";
  return `<tr><td>${name}</td><td class="r ${signCls(m.cagr_pct)}">${pct(m.cagr_pct)}</td><td class="r ${signCls(m.total_return_pct)}">${pct(m.total_return_pct)}</td>
    <td class="r">${num(m.volatility_pct, 1)}%</td><td class="r">${num(m.sharpe)}</td><td class="r">${num(m.sortino)}</td><td class="r down">${num(m.max_drawdown_pct, 1)}%</td><td class="r">${num(m.calmar)}</td></tr>`;
}
function renderBacktest(out, r, kind) {
  clearCharts();
  const m = r.metrics, isF = kind === "factor";
  const title = isF ? `多因子回測：${esc(r.preset)}` : `個股策略回測：${esc(r.code)} ${esc(r.name)}｜${esc(r.strategy)}`;
  const benchName = isF ? "0050 買進持有" : "買進持有";
  out.innerHTML = `
    <div class="card"><h3>${title}</h3>
      ${r.sources?.is_demo || r.source?.is_demo ? `<div class="err">此結果使用模擬資料，與真實市場無關。</div>` : ""}
      <div id="eqchart" class="chart md"></div>
      <div class="small muted">藍線：策略淨值；灰線：${benchName}（起點皆 = 1）</div>
      <div class="table-wrap"><table><thead><tr><th></th><th class="r">年化報酬</th><th class="r">總報酬</th><th class="r">年化波動</th><th class="r">Sharpe</th><th class="r">Sortino</th><th class="r">最大回撤</th><th class="r">Calmar</th></tr></thead><tbody>
        ${metricRow("策略", m)}${metricRow(benchName, isF ? r.benchmark_metrics : r.buy_hold_metrics)}
        ${metricRow(`樣本內（~${esc(r.split.split_date)}）`, r.split.in_sample)}${metricRow("樣本外", r.split.out_of_sample)}
      </tbody></table></div>
      <div class="kpis" style="margin-top:10px">
        ${isF ? `<div class="kpi"><div class="k">調倉次數</div><div class="v">${r.rebalances}</div></div>
          <div class="kpi"><div class="k">平均單次換手</div><div class="v">${num(r.avg_turnover_pct, 1)}%</div></div>
          <div class="kpi"><div class="k">累計交易成本</div><div class="v">${num(r.total_cost_pct, 2)}%</div></div>
          <div class="kpi"><div class="k">超額年化</div><div class="v ${signCls(r.excess_cagr_pct)}">${pct(r.excess_cagr_pct)}</div></div>
          <div class="kpi"><div class="k">月勝率</div><div class="v">${num(m.monthly_win_rate_pct, 0)}%</div></div>`
        : `<div class="kpi"><div class="k">交易次數</div><div class="v">${m.trades}</div></div>
          <div class="kpi"><div class="k">勝率</div><div class="v">${num(m.win_rate_pct, 0)}%</div></div>
          <div class="kpi"><div class="k">獲利因子</div><div class="v">${num(m.profit_factor)}</div></div>
          <div class="kpi"><div class="k">平均每筆</div><div class="v ${signCls(m.avg_trade_pct)}">${pct(m.avg_trade_pct, 2)}</div></div>
          <div class="kpi"><div class="k">持倉時間比</div><div class="v">${num(m.exposure_pct, 0)}%</div></div>`}
      </div>
      <h4>注意事項</h4><ul class="plain small">${r.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}${(r.notes || []).map((w) => `<li>${esc(w)}</li>`).join("")}</ul>
      ${isF ? `<p class="small muted">實際使用的因子權重：${Object.entries(r.factors_used || {}).map(([f, w]) => `${esc(state.overview?.factors[f] || f)} ${Math.round(w * 100)}%`).join("、")}</p>` : ""}
    </div>
    ${isF ? `<div class="card"><h3>最近調倉持股</h3><div class="table-wrap"><table><thead><tr><th>訊號日</th><th>成交日</th><th>持股（分數）</th></tr></thead><tbody>
      ${r.holdings.slice().reverse().map((h) => `<tr><td>${h.signal_date}</td><td>${h.trade_date}</td><td class="small">${h.picks.map((c) => `${esc(c)} ${esc(h.names[c])}（${num(h.scores[c], 0)}）`).join("、")}</td></tr>`).join("")}
      </tbody></table></div></div>`
    : `<div class="card"><h3>最近交易</h3><div class="table-wrap"><table><thead><tr><th>進場</th><th>出場</th><th class="r">進場價</th><th class="r">出場價</th><th>原因</th><th class="r">報酬</th></tr></thead><tbody>
      ${r.trades.slice().reverse().map((t) => `<tr><td>${t.entry_date}</td><td>${t.exit_date || "—"}</td><td class="r">${num(t.entry)}</td><td class="r">${num(t.exit)}</td><td>${esc(t.reason)}</td><td class="r ${signCls(t.return_pct)}">${pct(t.return_pct, 2)}</td></tr>`).join("")}
      </tbody></table></div>${src(r.source)}</div>`}`;
  const ch = makeChart($("#eqchart"), 300), c = themeColors();
  ch.addLineSeries({ color: c.accent, lineWidth: 2 }).setData(r.curve.map((x) => ({ time: x.time, value: x.strategy })));
  const b = r.curve.filter((x) => x.benchmark !== undefined);
  if (b.length) ch.addLineSeries({ color: c.muted, lineWidth: 1 }).setData(b.map((x) => ({ time: x.time, value: x.benchmark })));
  ch.timeScale().fitContent();
}

// ---------------------------------------------------------------- Agent
function md(text) {
  // 極簡 Markdown：先跳脫 HTML，再處理表格、標題、清單、粗體
  const lines = esc(text).split("\n"), out = [];
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (/^\s*\|.*\|\s*$/.test(l) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] || "")) {
      const cells = (s) => s.trim().replace(/^\||\|$/g, "").split("|").map((x) => x.trim());
      let html = `<div class="table-wrap"><table><thead><tr>${cells(l).map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>`;
      i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { html += `<tr>${cells(lines[i]).map((c) => `<td>${c}</td>`).join("")}</tr>`; i++; }
      out.push(html + "</tbody></table></div>"); i--; continue;
    }
    const h = l.match(/^(#{1,4})\s+(.*)$/);
    if (h) { out.push(`<h4>${h[2]}</h4>`); continue; }
    if (/^\s*[-*]\s+/.test(l)) { out.push(`<div>• ${l.replace(/^\s*[-*]\s+/, "")}</div>`); continue; }
    out.push(l ? `<div>${l}</div>` : "<br>");
  }
  return out.join("").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
}
function addMsg(role, html) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = html;
  $("#chat").appendChild(div);
  $("#chat").scrollTop = $("#chat").scrollHeight;
  return div;
}
$("#ask").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#question").value.trim();
  if (!q) return;
  $("#question").value = "";
  addMsg("user", esc(q));
  const bot = addMsg("bot", `<span class="spinner"></span>Agent 正在呼叫工具查資料…`);
  const btn = e.target.querySelector("button");
  await withBtn(btn, async () => {
    try {
      const r = await api("/api/agent", { method: "POST", body: JSON.stringify({ question: q, history: state.chat }) });
      if (r.error) { bot.innerHTML = `<div class="err">${esc(r.error)}</div>`; return; }
      state.chat.push({ role: "user", content: q }, { role: "assistant", content: r.answer });
      bot.style.whiteSpace = "normal";
      bot.innerHTML = md(r.answer) + `<details><summary>工具呼叫紀錄（${r.tool_calls.length} 次）— 回答中的數字都來自這些結果</summary><pre>${esc(r.tool_calls.map((c) => `${c.tool}(${JSON.stringify(c.input)})`).join("\n"))}</pre></details>`;
    } catch (err) { bot.innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  });
});
document.querySelectorAll(".chip[data-q]").forEach((c) => c.addEventListener("click", () => { $("#question").value = c.dataset.q; $("#ask").requestSubmit(); }));

loadOverview();
