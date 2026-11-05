"""多因子定義與排名（選股與回測共用同一套計算，確保「推薦用的就是回測過的邏輯」）。

所有因子在時間點 t 只使用 t（含）以前「已公開」的資料：
- 價格因子用 t 當日收盤以前的價格。
- 月營收在次月 10 日後才可使用（法定公布期限）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FACTORS = {
    "mom_6_1": {"name": "6 個月動能（扣近 1 月）", "direction": 1, "fmt": "pct"},
    "mom_12_1": {"name": "12 個月動能（扣近 1 月）", "direction": 1, "fmt": "pct"},
    "trend": {"name": "距半年線", "direction": 1, "fmt": "pct"},
    "low_vol": {"name": "60 日波動度（越低越好）", "direction": -1, "fmt": "pct"},
    "rev_yoy": {"name": "最新月營收年增率", "direction": 1, "fmt": "pct_raw"},
    "pe": {"name": "本益比（越低越好）", "direction": -1, "fmt": "x"},
    "pb": {"name": "股價淨值比（越低越好）", "direction": -1, "fmt": "x"},
    "dividend_yield": {"name": "殖利率", "direction": 1, "fmt": "pct_raw"},
    "roe": {"name": "近四季 ROE", "direction": 1, "fmt": "pct_raw"},
    "inst_flow": {"name": "法人 20 日買超 / 均量", "direction": 1, "fmt": "ratio"},
}

PRESETS = {
    "momentum": {
        "name": "動能趨勢",
        "desc": "強者恆強：中期動能、站上半年線、營收成長，搭配低波動避開暴漲暴跌股。多頭市場的主流做法。",
        "weights": {"mom_6_1": 0.35, "mom_12_1": 0.15, "trend": 0.2, "rev_yoy": 0.2, "low_vol": 0.1},
    },
    "value_growth": {
        "name": "價值成長",
        "desc": "估值合理且營收、獲利仍在成長（類 GARP）。盤整市場的主流做法。",
        "weights": {"pe": 0.25, "pb": 0.1, "rev_yoy": 0.3, "roe": 0.15, "mom_6_1": 0.2},
    },
    "dividend": {
        "name": "高股息存股",
        "desc": "殖利率高、波動低、ROE 穩定，適合存股族與現金流需求。",
        "weights": {"dividend_yield": 0.45, "low_vol": 0.25, "pb": 0.1, "roe": 0.2},
    },
    "defensive": {
        "name": "低波動防禦",
        "desc": "空頭或高波動時期，挑波動低、仍在趨勢上、具配息的股票。",
        "weights": {"low_vol": 0.45, "dividend_yield": 0.2, "roe": 0.15, "trend": 0.2},
    },
    "chips": {
        "name": "法人籌碼",
        "desc": "外資、投信、自營商近 20 日持續買超，配合動能與營收。需要逐檔抓法人資料，較慢。",
        "weights": {"inst_flow": 0.4, "mom_6_1": 0.3, "rev_yoy": 0.3},
    },
}


def market_regime(bench_close: pd.Series) -> dict:
    """依大盤代理（0050）判斷多空，決定「目前市場主流」的策略。"""
    s = bench_close.dropna()
    if len(s) < 220:
        return {"regime": "unknown", "preset": "value_growth", "reason": "大盤資料不足，使用價值成長"}
    ma200 = s.rolling(200).mean()
    last, m, m_prev = s.iloc[-1], ma200.iloc[-1], ma200.iloc[-21]
    ret60 = s.iloc[-1] / s.iloc[-61] - 1
    if last > m and m > m_prev:
        regime, preset = "bull", "momentum"
        reason = f"0050 在 200 日線之上且均線上揚（60 日 {ret60 * 100:+.1f}%）→ 多頭，主流為動能趨勢"
    elif last < m and m < m_prev:
        regime, preset = "bear", "defensive"
        reason = f"0050 在 200 日線之下且均線下彎（60 日 {ret60 * 100:+.1f}%）→ 空頭，主流為低波動防禦"
    else:
        regime, preset = "range", "value_growth"
        reason = f"0050 與 200 日線糾結（60 日 {ret60 * 100:+.1f}%）→ 盤整，主流為價值成長"
    return {"regime": regime, "preset": preset, "reason": reason,
            "bench_close": round(float(last), 2), "ma200": round(float(m), 2)}


def price_factors(close: pd.DataFrame, at: pd.Timestamp | None = None) -> pd.DataFrame:
    """close：日期 × 代號的收盤價寬表。回傳：代號 × 因子。"""
    c = close.loc[:at] if at is not None else close
    c = c.tail(260)
    out = pd.DataFrame(index=c.columns)

    def lag(n):
        return c.iloc[-n - 1] if len(c) > n else pd.Series(np.nan, index=c.columns)

    last = c.iloc[-1]
    out["mom_6_1"] = lag(21) / lag(126) - 1
    out["mom_12_1"] = lag(21) / lag(252) - 1
    out["trend"] = last / c.tail(120).mean() - 1
    out["low_vol"] = c.pct_change(fill_method=None).tail(60).std() * np.sqrt(252)
    # 最近一天沒有報價（停牌／下市）就不計
    out.loc[last.isna()] = np.nan
    return out


def revenue_yoy_at(rev: pd.DataFrame, at: pd.Timestamp) -> float | None:
    """at 當下已公布的最新月營收年增率（%）。"""
    avail = rev[rev["available_from"] <= at]
    if avail.empty:
        return None
    last = avail.iloc[-1]
    prev = avail[(avail["year"] == last["year"] - 1) & (avail["month"] == last["month"])]
    if prev.empty or not prev["revenue"].iloc[0]:
        return None
    return float((last["revenue"] / prev["revenue"].iloc[0] - 1) * 100)


def rank_scores(features: pd.DataFrame, weights: dict[str, float]) -> tuple[pd.DataFrame, dict]:
    """橫斷面百分位排名 → 加權總分（0~100）。

    - 每個因子先在選股池內取百分位（方向已校正，1 = 最好）。
    - 個股缺值給 0.5（中性），並記錄覆蓋率。
    - 全部股票都缺的因子直接剔除，權重重新分配。
    """
    usable = {f: w for f, w in weights.items() if f in features and features[f].notna().any()}
    dropped = sorted(set(weights) - set(usable))
    total = sum(usable.values()) or 1.0
    usable = {f: w / total for f, w in usable.items()}
    ranks = pd.DataFrame(index=features.index)
    for f in usable:
        x = features[f].astype(float)
        if f == "pe":  # 虧損（無本益比）視為最差
            x = x.where(x > 0)
            x = x.fillna(x.max() * 2 if x.notna().any() else np.nan)
        lo, hi = x.quantile(0.05), x.quantile(0.95)
        x = x.clip(lo, hi)
        r = x.rank(pct=True, ascending=FACTORS[f]["direction"] > 0)
        ranks[f] = r.fillna(0.5)
    score = sum(ranks[f] * w for f, w in usable.items()) * 100 if usable else pd.Series(50.0, index=features.index)
    ranks["score"] = score
    coverage = {f: round(float(features[f].notna().mean()), 3) for f in usable}
    return ranks.sort_values("score", ascending=False), {"used": usable, "dropped": dropped, "coverage": coverage}


def describe(f: str, value, pct_rank: float) -> str:
    meta = FACTORS[f]
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return f"{meta['name']}：無資料"
    if meta["fmt"] == "pct":
        v = f"{value * 100:+.1f}%"
    elif meta["fmt"] == "pct_raw":
        v = f"{value:.1f}%"
    elif meta["fmt"] == "x":
        v = f"{value:.1f} 倍"
    else:
        v = f"{value:.2f}"
    top = (1 - pct_rank) * 100
    return f"{meta['name']} {v}（池內前 {max(top, 1):.0f}%）"
