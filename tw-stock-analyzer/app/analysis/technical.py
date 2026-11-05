"""技術面多空訊號：把多個指標量化成 -100~+100 的分數，並排除「雜訊」訊號。

雜訊過濾規則：
- 價格變動小於 0.5 ATR 的突破／跌破不算數（盤整中的假突破）。
- 布林通道突破需要量能確認（成交量 > 20 日均量 1.5 倍）。
- KD 交叉只在低檔（<30）黃金交叉、高檔（>70）死亡交叉時採計。
"""
from __future__ import annotations

import math

import pandas as pd

from .indicators import add_all


def _f(x) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else round(x, 4)


def analyze(prices: pd.DataFrame) -> dict:
    if len(prices) < 70:
        return {"error": f"歷史資料只有 {len(prices)} 天，不足以計算技術指標（至少 70 天）"}
    df = add_all(prices)
    last, prev = df.iloc[-1], df.iloc[-2]
    close, atr_v = float(last["close"]), float(last["atr14"])
    signals: list[dict] = []

    def add(name: str, direction: int, weight: float, detail: str) -> None:
        signals.append({"name": name, "direction": direction, "weight": weight, "detail": detail})

    # 1. 均線排列（趨勢）
    if last["ma5"] > last["ma20"] > last["ma60"]:
        add("均線多頭排列", 1, 20, "MA5 > MA20 > MA60")
    elif last["ma5"] < last["ma20"] < last["ma60"]:
        add("均線空頭排列", -1, 20, "MA5 < MA20 < MA60")

    # 2. 季線（MA60）位置，需超過 0.5 ATR 才算
    gap = close - last["ma60"]
    if abs(gap) > 0.5 * atr_v:
        add("站上季線" if gap > 0 else "跌破季線", 1 if gap > 0 else -1, 15,
            f"收盤距 MA60 {gap / last['ma60'] * 100:+.1f}%")

    # 3. 年線（MA240）長期趨勢
    if pd.notna(last.get("ma240")):
        add("位於年線之上" if close > last["ma240"] else "位於年線之下",
            1 if close > last["ma240"] else -1, 10, f"MA240 = {last['ma240']:.2f}")

    # 4. MACD 柱狀體方向與交叉
    if prev["hist"] <= 0 < last["hist"]:
        add("MACD 黃金交叉", 1, 15, f"DIF {last['dif']:.2f} 上穿 DEA {last['dea']:.2f}")
    elif prev["hist"] >= 0 > last["hist"]:
        add("MACD 死亡交叉", -1, 15, f"DIF {last['dif']:.2f} 下穿 DEA {last['dea']:.2f}")
    else:
        add("MACD 柱狀體" + ("為正" if last["hist"] > 0 else "為負"), 1 if last["hist"] > 0 else -1, 8,
            f"OSC = {last['hist']:.2f}")

    # 5. RSI：過熱／超賣是「反向風險」提示，權重較低
    r = float(last["rsi14"])
    if r >= 80:
        add("RSI 過熱", -1, 8, f"RSI14 = {r:.1f}，短線追高風險")
    elif r <= 20:
        add("RSI 超賣", 1, 8, f"RSI14 = {r:.1f}，留意跌深反彈")
    elif r >= 55:
        add("RSI 偏強", 1, 6, f"RSI14 = {r:.1f}")
    elif r <= 45:
        add("RSI 偏弱", -1, 6, f"RSI14 = {r:.1f}")

    # 6. KD 交叉（只採計低檔黃金交叉、高檔死亡交叉）
    if prev["k"] <= prev["d"] and last["k"] > last["d"] and last["k"] < 30:
        add("KD 低檔黃金交叉", 1, 10, f"K {last['k']:.1f} / D {last['d']:.1f}")
    elif prev["k"] >= prev["d"] and last["k"] < last["d"] and last["k"] > 70:
        add("KD 高檔死亡交叉", -1, 10, f"K {last['k']:.1f} / D {last['d']:.1f}")

    # 7. 布林通道突破 + 量能確認
    vol_ratio = float(last["volume"] / last["vol_ma20"]) if last["vol_ma20"] else None
    if vol_ratio and vol_ratio > 1.5:
        if close > last["bb_upper"]:
            add("帶量突破布林上軌", 1, 12, f"量比 {vol_ratio:.2f}")
        elif close < last["bb_lower"]:
            add("帶量跌破布林下軌", -1, 12, f"量比 {vol_ratio:.2f}")

    # 8. 20 日動能
    mom20 = close / df["close"].iloc[-21] - 1
    if abs(mom20) > 0.03:
        add("20 日動能" + ("向上" if mom20 > 0 else "向下"), 1 if mom20 > 0 else -1, 10, f"{mom20 * 100:+.1f}%")

    total_w = sum(s["weight"] for s in signals) or 1
    score = round(sum(s["direction"] * s["weight"] for s in signals) / total_w * 100, 1)
    label = "偏多" if score >= 30 else "偏空" if score <= -30 else "中性"

    window = df.tail(60)
    return {
        "score": score,
        "label": label,
        "signals": signals,
        "latest": {
            "date": str(df.index[-1].date()),
            "close": _f(close),
            "change_pct": _f((close / prev["close"] - 1) * 100),
            **{k: _f(last[k]) for k in ("ma5", "ma20", "ma60", "ma240", "rsi14", "k", "d", "dif", "dea", "hist",
                                        "bb_upper", "bb_lower", "atr14")},
            "volume_ratio": _f(vol_ratio),
        },
        "support": _f(window["low"].min()),
        "resistance": _f(window["high"].max()),
    }


def chart_payload(prices: pd.DataFrame, days: int = 260) -> dict:
    df = add_all(prices).tail(days)
    t = [str(d.date()) for d in df.index]

    def series(col):
        return [{"time": ti, "value": round(float(v), 4)} for ti, v in zip(t, df[col]) if pd.notna(v)]

    return {
        "candles": [{"time": ti, "open": round(r.open, 4), "high": round(r.high, 4), "low": round(r.low, 4),
                     "close": round(r.close, 4)} for ti, r in zip(t, df.itertuples())],
        "volume": [{"time": ti, "value": float(v)} for ti, v in zip(t, df["volume"])],
        "ma20": series("ma20"), "ma60": series("ma60"), "ma240": series("ma240"),
        "rsi": series("rsi14"), "k": series("k"), "d": series("d"),
        "macd_hist": series("hist"),
    }
