"""模擬資料：只在 TWSA_DATA_MODE=demo 或測試時使用。

數字以股票代號為亂數種子產生，每次相同，但「與真實市場無關」。
所有回傳都標記 is_demo=True，前端會顯示醒目的「模擬資料」橫幅。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .base import Sourced
from .universe import label

SOURCE = "DEMO 模擬資料（非真實市場數據）"
END = pd.Timestamp("2026-11-04")


def _rng(code: str, salt: str = "") -> np.random.Generator:
    seed = int(hashlib.md5(f"{code}{salt}".encode()).hexdigest()[:8], 16)
    return np.random.default_rng(seed)


def _demo(data, as_of=None) -> Sourced:
    return Sourced(data, SOURCE, as_of, None, is_demo=True,
                   notes=["模擬資料僅供展示介面與流程，請勿作為任何投資判斷依據。"])


def price_history(code: str, start: str = "2019-01-01") -> Sourced:
    rng = _rng(code)
    dates = pd.bdate_range(pd.Timestamp("2018-01-01"), END)  # 固定起點，切片才會一致
    n = len(dates)
    drift = rng.normal(0.0004, 0.0004)
    vol = rng.uniform(0.011, 0.028)
    # 加一點趨勢段落，讓技術指標與回測有東西可看
    regime = np.repeat(rng.normal(0, 0.0012, n // 60 + 1), 60)[:n]
    rets = rng.normal(drift, vol, n) + regime
    close = rng.uniform(20, 900) * np.exp(np.cumsum(rets))
    spread = np.abs(rng.normal(0, vol / 1.5, n))
    open_ = close * np.exp(rng.normal(0, vol / 3, n))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    base_vol = rng.uniform(2e6, 4e7)
    volume = base_vol * np.exp(rng.normal(0, 0.4, n)) * (1 + 8 * np.abs(rets))
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)
    df.index.name = "date"
    df["turnover"] = df["close"] * df["volume"]
    df = df[df.index >= pd.Timestamp(start)]
    return _demo(df.round(4), str(END.date()))


def snapshot(codes: list[str]) -> Sourced:
    rows = []
    for code in codes:
        px = price_history(code).data
        rng = _rng(code, "val")
        last, prev = px.iloc[-1], px.iloc[-2]
        name, industry = label(code)
        eps_ttm = float(last["close"]) / rng.uniform(8, 35) * (1 if rng.random() > 0.08 else -0.3)
        rows.append({
            "code": code, "name": name, "industry": industry,
            "open": last["open"], "high": last["high"], "low": last["low"], "close": last["close"],
            "change": last["close"] - prev["close"], "volume": last["volume"], "turnover": last["turnover"],
            "pe": round(float(last["close"]) / eps_ttm, 2) if eps_ttm > 0 else None,
            "pb": round(rng.uniform(0.8, 7), 2),
            "dividend_yield": round(rng.uniform(0, 7), 2),
            **_latest_revenue(code),
            "date": str(END.date()),
        })
    return _demo(pd.DataFrame(rows), str(END.date()))


def _latest_revenue(code: str) -> dict:
    """讓「今日推薦」與「個股分析」看到同一組模擬營收。"""
    rev = monthly_revenue(code).data
    last, prev = rev.iloc[-1], rev.iloc[-2]
    yago = rev[(rev["year"] == last["year"] - 1) & (rev["month"] == last["month"])]["revenue"].iloc[0]
    return {"revenue_month": f"{int(last['year']) - 1911}{int(last['month']):02d}",
            "revenue_yoy": round((last["revenue"] / yago - 1) * 100, 2),
            "revenue_mom": round((last["revenue"] / prev["revenue"] - 1) * 100, 2)}


def monthly_revenue(code: str, start: str = "2019-01-01") -> Sourced:
    rng = _rng(code, "rev")
    months = pd.period_range(pd.Timestamp(start), END - pd.offsets.MonthBegin(2), freq="M")
    level = rng.uniform(5e8, 2e11)
    growth = np.cumsum(rng.normal(0.008, 0.03, len(months)))
    season = 1 + 0.08 * np.sin(np.arange(len(months)) * 2 * np.pi / 12)
    rev = level * np.exp(growth) * season
    df = pd.DataFrame({
        "year": months.year, "month": months.month, "revenue": rev.round(0),
        "available_from": (months + 1).to_timestamp() + pd.Timedelta(days=9),
    })
    return _demo(df, str(months[-1]))


def quarterly_financials(code: str, start: str = "2021-01-01") -> Sourced:
    rng = _rng(code, "fin")
    quarters = pd.period_range(pd.Timestamp(start), END, freq="Q")[:-1]
    rev = rng.uniform(5e9, 6e11) * np.exp(np.cumsum(rng.normal(0.02, 0.06, len(quarters))))
    gm = np.clip(rng.uniform(0.08, 0.55) + rng.normal(0, 0.015, len(quarters)), 0.01, 0.9)
    om = np.clip(gm - rng.uniform(0.03, 0.15), -0.1, 0.8)
    ni = rev * om * 0.82
    shares = rng.uniform(1e9, 2.6e10)
    assets = rev.mean() * rng.uniform(5, 14)
    debt_ratio = rng.uniform(0.2, 0.9 if code.startswith("28") else 0.6)
    df = pd.DataFrame({
        "revenue": rev, "gross_profit": rev * gm, "operating_income": rev * om,
        "net_income": ni, "eps": ni / shares,
        "total_assets": assets, "total_liabilities": assets * debt_ratio, "equity": assets * (1 - debt_ratio),
    }, index=quarters.to_timestamp(how="end").normalize())
    return _demo(df, str(quarters[-1]))


def institutional(code: str, start: str = "2026-06-01") -> Sourced:
    rng = _rng(code, "inst")
    dates = pd.bdate_range(pd.Timestamp(start), END)
    bias = rng.normal(0, 1)
    df = pd.DataFrame({
        "foreign": rng.normal(bias * 3e5, 2e6, len(dates)),
        "trust": rng.normal(bias * 5e4, 3e5, len(dates)),
        "dealer": rng.normal(0, 3e5, len(dates)),
    }, index=dates)
    df["total"] = df.sum(axis=1)
    return _demo(df.round(0), str(END.date()))


_NEWS = [
    ("{name}10月營收創同期新高，年增{n}%", "經濟日報"),
    ("{name}法說會釋利多，法人調升目標價", "鉅亨網"),
    ("{name}擴產計畫延後，下修全年資本支出", "工商時報"),
    ("外資連三日賣超{name}，股價跌破季線", "中央社"),
    ("{name}接單暢旺，第四季出貨可望續增", "MoneyDJ理財網"),
    ("明天必漲！老師免費報牌{name}，加LINE領取飆股名單", "某投顧部落格"),
    ("{name}主力進場！下一檔翻倍飆股就是它，限時加入會員", "財經達人群組"),
    ("{name}10月營收創同期新高 年增{n}%", "Yahoo股市"),
    ("{name}遭客戶砍單疑慮，毛利率恐承壓", "財訊"),
    ("{name}宣布配息，殖利率具吸引力", "自由財經"),
]


def news(code: str, start: str | None = None) -> Sourced:
    rng = _rng(code, "news")
    name, _ = label(code)
    picks = rng.choice(len(_NEWS), size=7, replace=False)
    now = datetime(2026, 11, 4, 18, 0)
    items = []
    for i, idx in enumerate(sorted(picks)):
        tpl, src = _NEWS[idx]
        items.append({
            "title": tpl.format(name=name, n=int(rng.integers(5, 60))),
            "link": "",
            "source": src,
            "published": (now - timedelta(hours=int(rng.integers(1, 120)))).isoformat(),
        })
    return _demo(items)


def market_news() -> Sourced:
    items = []
    for code in ("2330", "2317", "2454", "2603", "2881", "2308"):
        items.extend(news(code).data[:4])
    items.append({"title": "台股收盤上漲，外資買超擴大，電子權值股領漲", "link": "", "source": "中央社",
                  "published": "2026-11-04T14:00:00"})
    items.append({"title": "央行理監事會維持利率不變，房市管制續行", "link": "", "source": "經濟日報",
                  "published": "2026-11-04T09:00:00"})
    return _demo(items)
