"""Yahoo Finance chart API：還原權息的日線（作為回測與技術面的主要價格來源）。"""
from __future__ import annotations

import pandas as pd

from .base import DataUnavailable, Sourced, http_get

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def symbol(code: str) -> str:
    if code.startswith("^") or "." in code:
        return code
    return f"{code}.TW"


def parse_chart(payload: dict) -> pd.DataFrame:
    try:
        result = payload["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]
        ts = result["timestamp"]
    except (KeyError, IndexError, TypeError) as exc:
        err = (payload.get("chart") or {}).get("error")
        raise DataUnavailable(f"Yahoo 回傳格式不符：{err or exc}") from exc
    df = pd.DataFrame({
        "date": pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Taipei").tz_localize(None).normalize(),
        "open": quote.get("open"),
        "high": quote.get("high"),
        "low": quote.get("low"),
        "close": quote.get("close"),
        "volume": quote.get("volume"),
    })
    adj = (result["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    if adj is not None:
        df["adj_close"] = adj
        ratio = df["adj_close"] / df["close"]
        for col in ("open", "high", "low"):
            df[col] = df[col] * ratio
        df["close"] = df["adj_close"]
        df = df.drop(columns="adj_close")
    df = df.dropna(subset=["close"]).drop_duplicates("date", keep="last").set_index("date").sort_index()
    df["turnover"] = df["close"] * df["volume"]
    return df.astype(float)


def price_history(code: str, range_: str = "5y") -> Sourced:
    url = URL.format(symbol=symbol(code))
    try:
        payload = http_get(url, params={"range": range_, "interval": "1d", "events": "div,splits"}).json()
    except Exception as exc:
        raise DataUnavailable(f"Yahoo {symbol(code)} 取得失敗：{exc}") from exc
    df = parse_chart(payload)
    if df.empty:
        raise DataUnavailable(f"Yahoo {symbol(code)} 無資料")
    src = Sourced(df, "Yahoo Finance（還原權息）", str(df.index.max().date()), url)
    src.notes.append("成交金額為「還原收盤價 × 成交量」的估計值。")
    return src
