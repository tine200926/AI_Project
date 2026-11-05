"""技術指標（純 pandas 實作，結果可與常見看盤軟體對照）。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI。"""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    dif = ema(close, fast) - ema(close, slow)
    dea = dif.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"dif": dif, "dea": dea, "hist": dif - dea})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    std = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"mid": mid, "upper": mid + k * std, "lower": mid - k * std})


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def kd(df: pd.DataFrame, n: int = 9) -> pd.DataFrame:
    """台股慣用的 KD（RSV 9 日，K、D 以 1/3 平滑，起始值 50）。"""
    low_n = df["low"].rolling(n, min_periods=n).min()
    high_n = df["high"].rolling(n, min_periods=n).max()
    rsv = ((df["close"] - low_n) / (high_n - low_n).replace(0, np.nan) * 100).fillna(50)
    k_vals, d_vals = [], []
    k = d = 50.0
    for value, ready in zip(rsv, low_n.notna()):
        if ready:
            k = k * 2 / 3 + value / 3
            d = d * 2 / 3 + k / 3
        k_vals.append(k if ready else np.nan)
        d_vals.append(d if ready else np.nan)
    return pd.DataFrame({"k": k_vals, "d": d_vals}, index=df.index)


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for n in (5, 10, 20, 60, 120, 240):
        out[f"ma{n}"] = sma(out["close"], n)
    out["rsi14"] = rsi(out["close"])
    out = out.join(macd(out["close"]))
    out = out.join(bollinger(out["close"]).add_prefix("bb_"))
    out["atr14"] = atr(out)
    out = out.join(kd(out))
    out["vol_ma20"] = sma(out["volume"], 20)
    return out
