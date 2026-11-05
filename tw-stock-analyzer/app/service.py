"""把資料層與分析模組組合成 API 用的結果。每個區塊都附資料來源；取不到就明講。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from .analysis import backtest as bt
from .analysis import fundamentals, risk, sentiment, technical
from .analysis.factors import FACTORS, PRESETS, describe, market_regime, price_factors, rank_scores
from .config import settings
from .data import provider
from .data.base import DataUnavailable, Sourced
from .data.universe import BENCHMARK, DEFAULT_UNIVERSE, label

DISCLAIMER = ("本網站內容僅為量化研究與資訊整理，不構成任何投資建議或買賣邀約。"
              "投資有風險，請自行判斷並承擔盈虧。")


def _fetch_many(fn, codes: list[str], workers: int = 8) -> tuple[dict[str, Sourced], dict[str, str]]:
    ok, err = {}, {}

    def one(c):
        try:
            return c, fn(c), None
        except DataUnavailable as exc:
            return c, None, str(exc)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for c, res, e in ex.map(one, codes):
            if res is not None:
                ok[c] = res
            else:
                err[c] = e
    return ok, err


def _section(fn):
    """執行一個分析區塊；失敗時回傳錯誤說明而不是讓整頁壞掉。"""
    try:
        return fn()
    except DataUnavailable as exc:
        return {"error": str(exc)}


def _bench(years: float = 5) -> Sourced | None:
    try:
        return provider.prices(BENCHMARK, years)
    except DataUnavailable:
        return None


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if np.isnan(f) or np.isinf(f) else f
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return str(obj.date())
    return obj


# ------------------------------------------------------------------ 大盤總覽

def overview() -> dict:
    bench = _bench(2)
    regime = market_regime(bench.data["close"]) if bench else {"regime": "unknown", "preset": "value_growth",
                                                                 "reason": "無法取得 0050 價格"}
    out = {"mode": settings.data_mode, "ai_enabled": settings.ai_enabled, "disclaimer": DISCLAIMER,
           "regime": regime, "presets": {k: {"name": v["name"], "desc": v["desc"], "weights": v["weights"]}
                                         for k, v in PRESETS.items()},
           "factors": {k: v["name"] for k, v in FACTORS.items()},
           "strategies": {k: v for k, v in bt.STRATEGIES.items()},
           "universe": [{"code": c, "name": n, "industry": i} for c, (n, i) in DEFAULT_UNIVERSE.items()]}
    if bench:
        out["benchmark"] = {**bench.meta(), "technical": technical.analyze(bench.data)}
    return _clean(out)


# ------------------------------------------------------------------ 選股推薦

def screen(preset: str = "auto", codes: list[str] | None = None, top_n: int = 10,
           min_turnover: float = risk.LOW_LIQUIDITY) -> dict:
    codes = codes or list(DEFAULT_UNIVERSE)
    bench = _bench(2)
    regime = market_regime(bench.data["close"]) if bench else None
    chosen = preset
    if preset == "auto":
        chosen = regime["preset"] if regime else "value_growth"
    if chosen not in PRESETS:
        raise ValueError(f"未知策略：{preset}")
    weights = PRESETS[chosen]["weights"]

    prices, price_err = _fetch_many(lambda c: provider.prices(c, 2), codes)
    if not prices:
        raise DataUnavailable("所有股票的價格都取不到：" + "；".join(list(price_err.values())[:3]))
    close = pd.DataFrame({c: s.data["close"] for c, s in prices.items()}).sort_index()
    feats = price_factors(close)
    notes = []

    try:
        snap = provider.snapshot(list(prices))
        sdf = snap.data.set_index("code")
        for col in ("pe", "pb", "dividend_yield"):
            if col in sdf:
                feats[col] = sdf[col].reindex(feats.index).astype(float)
        if "revenue_yoy" in sdf:
            feats["rev_yoy"] = sdf["revenue_yoy"].reindex(feats.index).astype(float)
        names = sdf["name"].to_dict() if "name" in sdf else {}
        industries = sdf["industry"].to_dict() if "industry" in sdf else {}
        notes += snap.notes
        snap_meta = snap.meta()
    except DataUnavailable as exc:
        names, industries, snap_meta = {}, {}, None
        notes.append(f"TWSE 當日資料取得失敗，估值與營收因子無法使用：{exc}")

    if "roe" in weights:
        fins, _ = _fetch_many(provider.financials, list(prices))
        feats["roe"] = pd.Series({c: fundamentals.financial_metrics(s.data).get("roe_ttm") for c, s in fins.items()},
                                 dtype=float)
    if "inst_flow" in weights:
        inst, _ = _fetch_many(provider.institutional, list(prices))
        flow = {}
        for c, s in inst.items():
            vol20 = prices[c].data["volume"].tail(20).mean()
            flow[c] = float(s.data["total"].tail(20).sum() / vol20) if vol20 else np.nan
        feats["inst_flow"] = pd.Series(flow, dtype=float)

    try:
        warn = provider.warnings().data
    except DataUnavailable:
        warn = {"disposition": None, "attention": None}

    excluded = []
    keep = []
    for c in feats.index:
        df = prices[c].data
        liq = float(df["turnover"].tail(20).mean()) if "turnover" in df else None
        if warn.get("disposition") and c in warn["disposition"]:
            excluded.append({"code": c, "name": names.get(c, label(c)[0]), "reason": "處置股票"})
        elif warn.get("attention") and c in warn["attention"]:
            excluded.append({"code": c, "name": names.get(c, label(c)[0]), "reason": "注意股票"})
        elif liq is not None and liq < min_turnover:
            excluded.append({"code": c, "name": names.get(c, label(c)[0]),
                             "reason": f"流動性不足（20 日均成交 {liq / 1e6:.0f} 百萬元）"})
        elif pd.isna(feats.loc[c, "mom_6_1"]):
            excluded.append({"code": c, "name": names.get(c, label(c)[0]), "reason": "上市未滿半年，歷史不足"})
        else:
            keep.append(c)
    if not keep:
        raise DataUnavailable("所有股票都被風險過濾排除")

    ranked, info = rank_scores(feats.loc[keep], weights)
    bench_close = bench.data["close"] if bench else None
    picks = []
    for c in ranked.index[:top_n]:
        df = prices[c].data
        rk = risk.assess(df, bench_close, code=c, warnings=warn)
        tech = technical.analyze(df)
        contrib = sorted(info["used"], key=lambda f: -ranked.loc[c, f] * info["used"][f])
        reasons = [describe(f, feats.loc[c, f] if f in feats else None, ranked.loc[c, f]) for f in contrib[:3]]
        weak = [describe(f, feats.loc[c, f] if f in feats else None, ranked.loc[c, f])
                for f in info["used"] if ranked.loc[c, f] < 0.3]
        picks.append({
            "code": c, "name": names.get(c, label(c)[0]), "industry": industries.get(c, label(c)[1]),
            "score": round(float(ranked.loc[c, "score"]), 1),
            "close": round(float(df["close"].iloc[-1]), 2),
            "factor_ranks": {f: round(float(ranked.loc[c, f]), 3) for f in info["used"]},
            "factor_values": {f: (None if pd.isna(feats.loc[c, f]) else float(feats.loc[c, f]))
                              for f in info["used"] if f in feats},
            "reasons": reasons, "weaknesses": weak,
            "technical": {"score": tech.get("score"), "label": tech.get("label")},
            "risk": {"level": rk["level"], "score": rk["score"], "flags": rk["flags"],
                     "stop_loss": rk["plan"]["stop_loss"], "stop_loss_pct": rk["plan"]["stop_loss_pct"]},
        })

    sectors: dict[str, int] = {}
    for p in picks:
        sectors[p["industry"]] = sectors.get(p["industry"], 0) + 1
    conc = [f"{k} 佔 {v} 檔" for k, v in sectors.items() if v / max(len(picks), 1) > 0.4]
    if conc:
        notes.append("產業集中度偏高：" + "、".join(conc) + "，建議分散或降低單一產業權重。")
    if price_err:
        notes.append(f"{len(price_err)} 檔價格取得失敗已略過：" + ", ".join(price_err))

    return _clean({
        "preset": chosen, "preset_name": PRESETS[chosen]["name"], "preset_desc": PRESETS[chosen]["desc"],
        "regime": regime, "weights": info["used"], "factors_dropped": info["dropped"], "coverage": info["coverage"],
        "picks": picks, "excluded": excluded, "universe_size": len(codes), "eligible": len(keep),
        "sources": {"prices": sorted({s.source for s in prices.values()}), "snapshot": snap_meta,
                    "is_demo": any(s.is_demo for s in prices.values())},
        "as_of": str(close.index[-1].date()), "notes": notes, "disclaimer": DISCLAIMER,
    })


# ------------------------------------------------------------------ 個股分析

def stock(code: str, capital: float = 1_000_000, risk_pct: float = 1.0, atr_mult: float = 2.0) -> dict:
    code = code.strip().upper()
    px = provider.prices(code, 3)
    bench = _bench(2)
    df = px.data
    snap_row: dict = {}
    snap_meta = None
    try:
        snap = provider.snapshot([code])
        if len(snap.data):
            snap_row = snap.data.iloc[0].to_dict()
            snap_meta = snap.meta()
    except DataUnavailable as exc:
        snap_meta = {"error": str(exc)}
    name = snap_row.get("name") or label(code)[0]

    fin = _section(lambda: provider.financials(code))
    rev = _section(lambda: provider.revenue_history(code))
    fund = fundamentals.summarize(
        fin.data if isinstance(fin, Sourced) else None,
        rev.data if isinstance(rev, Sourced) else None,
        {k: snap_row.get(k) for k in ("pe", "pb", "dividend_yield")} if snap_row else None,
    )
    fund["sources"] = {
        "financials": fin.meta() if isinstance(fin, Sourced) else fin,
        "revenue": rev.meta() if isinstance(rev, Sourced) else rev,
        "valuation": snap_meta,
    }

    try:
        warn = provider.warnings().data
    except DataUnavailable:
        warn = {"disposition": None, "attention": None}
    rk = risk.assess(df, bench.data["close"] if bench else None, code=code, warnings=warn,
                     fundamentals=fund, capital=capital, risk_pct=risk_pct, atr_mult=atr_mult)

    news = _section(lambda: provider.news(code, name))
    if isinstance(news, Sourced):
        senti = sentiment.analyze(news.data, keywords=[name, code])
        senti["source"] = news.meta()
    else:
        senti = news

    inst = _section(lambda: provider.institutional(code))
    inst_out = inst
    if isinstance(inst, Sourced):
        t = inst.data.tail(20)
        inst_out = {"sum_20d": {k: float(t[k].sum()) for k in ("foreign", "trust", "dealer", "total")},
                    "sum_5d": {k: float(inst.data[k].tail(5).sum()) for k in ("foreign", "trust", "dealer", "total")},
                    "source": inst.meta()}

    tech = technical.analyze(df)
    verdict = _verdict(tech, senti, fund, rk)
    return _clean({
        "code": code, "name": name, "industry": snap_row.get("industry") or label(code)[1],
        "price_source": px.meta(), "technical": tech, "chart": technical.chart_payload(df),
        "fundamentals": fund, "risk": rk, "sentiment": senti, "institutional": inst_out,
        "verdict": verdict, "disclaimer": DISCLAIMER,
    })


def _verdict(tech: dict, senti: dict, fund: dict, rk: dict) -> dict:
    """綜合多空：技術 50%、輿情 20%、基本面 30%；高風險時下修並註明。"""
    parts = []
    t = tech.get("score")
    if t is not None:
        parts.append(("技術面", t, 0.5))
    s = senti.get("score") if isinstance(senti, dict) and "error" not in senti else None
    if s is not None and senti.get("confidence") != "低":
        parts.append(("新聞輿情", s, 0.2))
    tones = [h["tone"] for h in fund.get("highlights", [])]
    if tones:
        f = (tones.count("pos") - tones.count("neg")) / len(tones) * 100
        parts.append(("基本面", f, 0.3))
    if not parts:
        return {"score": None, "label": "資料不足", "parts": []}
    wsum = sum(w for _, _, w in parts)
    score = sum(v * w for _, v, w in parts) / wsum
    label_ = "偏多" if score >= 25 else "偏空" if score <= -25 else "中性"
    caution = None
    if rk["level"] in ("高", "極高"):
        caution = f"風險等級「{rk['level']}」：即使訊號偏多也應降低部位或等待風險解除。"
    return {"score": round(score, 1), "label": label_, "caution": caution,
            "parts": [{"name": n, "score": round(v, 1), "weight": w} for n, v, w in parts]}


# ------------------------------------------------------------------ 新聞

def market_news(days: int = 1) -> dict:
    res = provider.market_news(days)
    out = sentiment.analyze(res.data)
    out["source"] = res.meta()
    return _clean(out)


# ------------------------------------------------------------------ 回測

def backtest_factor(preset: str = "momentum", codes: list[str] | None = None, years: float = 5,
                    top_n: int = 10, fee_discount: float = 1.0, use_revenue: bool = True,
                    use_valuation: bool = False, min_turnover: float = risk.LOW_LIQUIDITY) -> dict:
    codes = codes or list(DEFAULT_UNIVERSE)
    prices, err = _fetch_many(lambda c: provider.prices(c, years + 1.1), codes)
    if len(prices) < top_n + 2:
        raise DataUnavailable(f"可用股票只有 {len(prices)} 檔，不足以回測 top {top_n}")
    if preset not in PRESETS:
        raise ValueError(f"未知策略：{preset}（可用：{', '.join(PRESETS)}）")
    bench = _bench(years + 1.1)
    weights = PRESETS[preset]["weights"]
    revenue = valuation = None
    notes = []
    if use_revenue and "rev_yoy" in weights:
        revs, rerr = _fetch_many(provider.revenue_history, list(prices))
        revenue = {c: s.data for c, s in revs.items()} or None
        if rerr:
            notes.append(f"{len(rerr)} 檔月營收取得失敗")
    if use_valuation and {"pe", "pb", "dividend_yield"} & set(weights):
        vals, verr = _fetch_many(lambda c: provider.valuation_history(c, years + 1.1), list(prices))
        valuation = {c: s.data for c, s in vals.items()} or None
        if verr:
            notes.append(f"{len(verr)} 檔本益比歷史取得失敗")
    start = (pd.Timestamp.today() - pd.DateOffset(days=int(365.25 * years))).strftime("%Y-%m-%d")
    if any(s.is_demo for s in prices.values()):
        from .data.demo import END
        start = (END - pd.DateOffset(days=int(365.25 * years))).strftime("%Y-%m-%d")
    res = bt.factor_backtest({c: s.data for c, s in prices.items()}, preset, top_n=top_n, start=start,
                             costs=bt.Costs(fee_discount=fee_discount),
                             bench=bench.data["close"] if bench else None, revenue=revenue,
                             valuation=valuation, min_turnover=min_turnover)
    for h in res["holdings"]:
        h["names"] = {c: label(c)[0] for c in h["picks"]}
    res["notes"] = notes + ([f"{len(err)} 檔價格取得失敗已略過"] if err else [])
    res["sources"] = {"prices": sorted({s.source for s in prices.values()}),
                      "is_demo": any(s.is_demo for s in prices.values()),
                      "benchmark": BENCHMARK if bench else None}
    res["disclaimer"] = DISCLAIMER
    return _clean(res)


def backtest_strategy(code: str, strategy: str = "ma_cross", params: dict | None = None,
                      years: float = 5, stop_atr: float | None = 2.5, fee_discount: float = 1.0) -> dict:
    px = provider.prices(code, years)
    res = bt.strategy_backtest(px.data, strategy, params, code=code, stop_atr=stop_atr,
                               costs=bt.Costs(fee_discount=fee_discount))
    res["code"], res["name"] = code, label(code)[0]
    res["source"] = px.meta()
    res["disclaimer"] = DISCLAIMER
    return _clean(res)
