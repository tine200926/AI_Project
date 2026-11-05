"""選用：FinLab 資料源（https://www.finlab.tw，需 `pip install finlab` 與 FINLAB_API_TOKEN）。

FinLab 的資料是「全市場寬表」（日期 × 股票代號），一次下載後在本地快取，
適合用來做全市場多因子回測。沒有安裝或沒有 token 時，此模組不會被使用。
"""
from __future__ import annotations

import pandas as pd

from ..config import settings
from .base import DataUnavailable, Sourced

_TABLES = {
    "open": "price:開盤價",
    "high": "price:最高價",
    "low": "price:最低價",
    "close": "price:收盤價",
    "volume": "price:成交股數",
    "turnover": "price:成交金額",
}


def available() -> bool:
    if not settings.finlab_token:
        return False
    try:
        import finlab  # noqa: F401
    except ImportError:
        return False
    return True


def _data():
    import finlab
    from finlab import data

    finlab.login(settings.finlab_token)
    return data


def price_history(code: str, start: str) -> Sourced:
    if not available():
        raise DataUnavailable("FinLab 未設定")
    try:
        data = _data()
        frames = {k: data.get(v)[code] for k, v in _TABLES.items()}
    except Exception as exc:
        raise DataUnavailable(f"FinLab {code} 取得失敗：{exc}") from exc
    df = pd.DataFrame(frames).dropna(subset=["close"])
    df.index = pd.to_datetime(df.index)
    df = df[df.index >= pd.Timestamp(start)].astype(float)
    if df.empty:
        raise DataUnavailable(f"FinLab {code} 無資料")
    src = Sourced(df, "FinLab price:*", str(df.index.max().date()), "https://www.finlab.tw")
    src.notes.append("未還原權息價格。")
    return src
