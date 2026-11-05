"""FinMind 開源資料 API（https://finmindtrade.com）。

提供個股「歷史」資料：日線、月營收、季財報、資產負債表、三大法人、本益比歷史、新聞。
免費帳號即可使用；設定 FINMIND_TOKEN 可提高每小時呼叫上限。
"""
from __future__ import annotations

import pandas as pd

from ..config import settings
from .base import DataUnavailable, Sourced, http_get

URL = "https://api.finmindtrade.com/api/v4/data"


def fetch(dataset: str, data_id: str, start_date: str, end_date: str | None = None) -> pd.DataFrame:
    params = {"dataset": dataset, "data_id": data_id, "start_date": start_date}
    if end_date:
        params["end_date"] = end_date
    headers = {"Authorization": f"Bearer {settings.finmind_token}"} if settings.finmind_token else {}
    try:
        payload = http_get(URL, params=params, headers=headers).json()
    except Exception as exc:
        raise DataUnavailable(f"FinMind {dataset}({data_id}) 取得失敗：{exc}") from exc
    if payload.get("status") not in (200, None) or "data" not in payload:
        raise DataUnavailable(f"FinMind {dataset}({data_id})：{payload.get('msg', '未知錯誤')}")
    df = pd.DataFrame(payload["data"])
    if df.empty:
        raise DataUnavailable(f"FinMind {dataset}({data_id}) 無資料")
    return df


def _src(dataset: str, df: pd.DataFrame, col: str = "date") -> Sourced:
    as_of = str(df[col].max()) if col in df else None
    return Sourced(df, f"FinMind {dataset}", as_of, URL)


def price_history(code: str, start: str) -> Sourced:
    df = fetch("TaiwanStockPrice", code, start)
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"]),
        "open": df["open"].astype(float),
        "high": df["max"].astype(float),
        "low": df["min"].astype(float),
        "close": df["close"].astype(float),
        "volume": df["Trading_Volume"].astype(float),
        "turnover": df["Trading_money"].astype(float),
    })
    out = out[out["close"] > 0].set_index("date").sort_index()
    src = _src("TaiwanStockPrice", df)
    src.data = out
    src.notes.append("未還原權息價格：除權息日會出現價格缺口，回測報酬會低估股利。")
    return src


def monthly_revenue(code: str, start: str) -> Sourced:
    df = fetch("TaiwanStockMonthRevenue", code, start)
    out = pd.DataFrame({
        "year": df["revenue_year"].astype(int),
        "month": df["revenue_month"].astype(int),
        "revenue": df["revenue"].astype(float),
        # FinMind 的 date 欄是「公布月」（營收月份的下個月 1 日），用來避免回測偷看未來
        "available_from": pd.to_datetime(df["date"]) + pd.Timedelta(days=9),
    }).sort_values(["year", "month"]).reset_index(drop=True)
    src = _src("TaiwanStockMonthRevenue", df)
    src.data = out
    return src


INCOME_KEYS = {
    "revenue": ("Revenue", "OperatingRevenue"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncome",),
    "net_income": ("IncomeAfterTaxes", "NetIncome", "IncomeFromContinuingOperations"),
    "net_income_parent": ("EquityAttributableToOwnersOfParent",),
    "eps": ("EPS",),
}
BALANCE_KEYS = {
    "total_assets": ("TotalAssets",),
    "total_liabilities": ("Liabilities", "TotalLiabilities"),
    "equity": ("Equity", "TotalEquity"),
    "current_assets": ("CurrentAssets",),
    "current_liabilities": ("CurrentLiabilities",),
}


def _pivot(df: pd.DataFrame, keys: dict) -> pd.DataFrame:
    wide = df.pivot_table(index="date", columns="type", values="value", aggfunc="last")
    out = pd.DataFrame(index=wide.index)
    for col, candidates in keys.items():
        for c in candidates:
            if c in wide.columns:
                out[col] = wide[c].astype(float)
                break
    out.index = pd.to_datetime(out.index)
    return out.sort_index()


def quarterly_financials(code: str, start: str) -> Sourced:
    inc = fetch("TaiwanStockFinancialStatements", code, start)
    out = _pivot(inc, INCOME_KEYS)
    try:
        bal = fetch("TaiwanStockBalanceSheet", code, start)
        out = out.join(_pivot(bal, BALANCE_KEYS), how="left")
    except DataUnavailable:
        pass
    src = _src("TaiwanStockFinancialStatements / TaiwanStockBalanceSheet", inc)
    src.data = out
    return src


def valuation_history(code: str, start: str) -> Sourced:
    df = fetch("TaiwanStockPER", code, start)
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"]),
        "pe": pd.to_numeric(df["PER"], errors="coerce"),
        "pb": pd.to_numeric(df["PBR"], errors="coerce"),
        "dividend_yield": pd.to_numeric(df["dividend_yield"], errors="coerce"),
    }).set_index("date").sort_index()
    src = _src("TaiwanStockPER", df)
    src.data = out
    return src


def institutional(code: str, start: str) -> Sourced:
    df = fetch("TaiwanStockInstitutionalInvestorsBuySell", code, start)
    df["net"] = df["buy"].astype(float) - df["sell"].astype(float)
    wide = df.pivot_table(index="date", columns="name", values="net", aggfunc="sum").fillna(0.0)
    wide.index = pd.to_datetime(wide.index)
    out = pd.DataFrame(index=wide.index)
    out["foreign"] = wide[[c for c in wide.columns if c.startswith("Foreign")]].sum(axis=1)
    out["trust"] = wide[[c for c in wide.columns if c.startswith("Investment_Trust")]].sum(axis=1)
    out["dealer"] = wide[[c for c in wide.columns if c.startswith("Dealer")]].sum(axis=1)
    out["total"] = out.sum(axis=1)
    src = _src("TaiwanStockInstitutionalInvestorsBuySell", df)
    src.data = out.sort_index()
    return src


def news(code: str, start: str) -> Sourced:
    df = fetch("TaiwanStockNews", code, start)
    items = [
        {"title": r.get("title", ""), "link": r.get("link", ""), "source": r.get("source", ""),
         "published": str(r.get("date", ""))}
        for r in df.to_dict("records")
    ]
    return Sourced(items, "FinMind TaiwanStockNews", str(df["date"].max()), URL)
