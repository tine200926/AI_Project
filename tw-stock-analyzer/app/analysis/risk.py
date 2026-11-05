"""風險控管：風險等級標註、ATR 停損、依風險預算計算部位大小。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .indicators import atr

LOW_LIQUIDITY = 5e7       # 20 日均成交金額 < 5,000 萬：流動性偏低
VERY_LOW_LIQUIDITY = 1e7  # < 1,000 萬：流動性極低


def _r(x, nd=2):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), nd)


def max_drawdown(close: pd.Series) -> float:
    peak = close.cummax()
    return float((close / peak - 1).min())


def beta(stock: pd.Series, bench: pd.Series) -> float | None:
    rets = pd.concat([stock.pct_change(), bench.pct_change()], axis=1, join="inner").dropna()
    if len(rets) < 60 or rets.iloc[:, 1].var() == 0:
        return None
    return float(rets.cov().iloc[0, 1] / rets.iloc[:, 1].var())


def position_size(capital: float, entry: float, stop: float, risk_pct: float = 1.0,
                  max_weight_pct: float = 10.0) -> dict:
    """風險預算法：單筆停損時最多虧損 capital × risk_pct%，且單一部位不超過 max_weight_pct%。"""
    per_share_risk = entry - stop
    if per_share_risk <= 0 or entry <= 0:
        return {"shares": 0, "lots": 0, "odd_shares": 0, "note": "停損價需低於進場價"}
    by_risk = capital * risk_pct / 100 / per_share_risk
    by_weight = capital * max_weight_pct / 100 / entry
    shares = int(min(by_risk, by_weight))
    return {
        "shares": shares,
        "lots": shares // 1000,
        "odd_shares": shares % 1000,
        "position_value": round(shares * entry),
        "weight_pct": _r(shares * entry / capital * 100),
        "max_loss": round(shares * per_share_risk),
        "binding": "單一部位上限" if by_weight < by_risk else "風險預算",
    }


def assess(prices: pd.DataFrame, bench: pd.Series | None = None, *, code: str = "",
           warnings: dict | None = None, fundamentals: dict | None = None,
           capital: float = 1_000_000, risk_pct: float = 1.0, atr_mult: float = 2.0) -> dict:
    close = prices["close"]
    rets = close.pct_change().dropna()
    one_year = close.tail(250)
    vol_60 = float(rets.tail(60).std() * np.sqrt(252))
    mdd_1y = max_drawdown(one_year)
    atr_v = float(atr(prices).iloc[-1])
    last = float(close.iloc[-1])
    turnover_20 = float(prices["turnover"].tail(20).mean()) if "turnover" in prices else None
    b = beta(close.tail(250), bench.tail(250)) if bench is not None else None

    flags: list[dict] = []
    score = 0

    def flag(level: str, text: str, pts: int) -> None:
        nonlocal score
        flags.append({"level": level, "text": text})
        score += pts

    w = warnings or {}
    if w.get("disposition") is None or w.get("attention") is None:
        flag("info", "處置／注意股票清單未取得，無法確認是否被列管", 0)
    if w.get("disposition") and code in w["disposition"]:
        flag("high", "列為處置股票（分盤撮合、可能需預收款券）", 45)
    if w.get("attention") and code in w["attention"]:
        flag("mid", "列為注意股票（交易異常）", 20)

    if vol_60 > 0.6:
        flag("high", f"60 日年化波動 {vol_60 * 100:.0f}%，屬極高波動", 30)
    elif vol_60 > 0.4:
        flag("mid", f"60 日年化波動 {vol_60 * 100:.0f}%，波動偏高", 18)
    elif vol_60 > 0.25:
        score += 8

    if mdd_1y < -0.4:
        flag("high", f"近一年最大回撤 {mdd_1y * 100:.0f}%", 20)
    elif mdd_1y < -0.25:
        flag("mid", f"近一年最大回撤 {mdd_1y * 100:.0f}%", 10)

    if turnover_20 is not None:
        if turnover_20 < VERY_LOW_LIQUIDITY:
            flag("high", f"20 日均成交金額約 {turnover_20 / 1e6:.1f} 百萬元，流動性極低（滑價風險）", 25)
        elif turnover_20 < LOW_LIQUIDITY:
            flag("mid", f"20 日均成交金額約 {turnover_20 / 1e6:.1f} 百萬元，流動性偏低", 12)

    if b is not None and b > 1.5:
        flag("mid", f"Beta {b:.2f}，對大盤漲跌放大", 8)

    fm = (fundamentals or {}).get("financials") or {}
    if fm.get("eps_ttm") is not None and fm["eps_ttm"] <= 0:
        flag("mid", "近四季 EPS 為負（虧損）", 15)
    if fm.get("debt_ratio") is not None and fm["debt_ratio"] > 75 and not code.startswith("28"):
        flag("mid", f"負債比 {fm['debt_ratio']:.0f}%（非金融業）偏高", 10)
    if fundamentals is not None and fundamentals.get("missing"):
        flag("info", "缺少資料：" + "、".join(fundamentals["missing"]) + "，基本面風險無法完整評估", 5)

    score = min(score, 100)
    level = "極高" if score >= 60 else "高" if score >= 35 else "中" if score >= 15 else "低"
    stop = last - atr_mult * atr_v
    return {
        "level": level,
        "score": score,
        "flags": flags,
        "metrics": {
            "volatility_60d_ann_pct": _r(vol_60 * 100),
            "max_drawdown_1y_pct": _r(mdd_1y * 100),
            "atr14": _r(atr_v),
            "atr_pct": _r(atr_v / last * 100),
            "beta_1y": _r(b),
            "avg_turnover_20d": _r(turnover_20, 0),
        },
        "plan": {
            "entry_ref": _r(last),
            "stop_loss": _r(stop),
            "stop_loss_pct": _r((stop / last - 1) * 100),
            "take_profit_2r": _r(last + 2 * (last - stop)),
            "trailing_rule": f"收盤跌破「進場後最高收盤 − {atr_mult:g}×ATR」出場",
            "atr_mult": atr_mult,
            "capital": capital,
            "risk_pct": risk_pct,
            "sizing": position_size(capital, last, stop, risk_pct),
        },
    }
