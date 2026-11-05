"""臺灣證券交易所 OpenAPI（https://openapi.twse.com.tw，免費、免金鑰）。

提供全市場「最新一日」的橫斷面資料：收盤行情、本益比／淨值比／殖利率、
月營收、最近一季財報，以及處置／注意股票公告。
欄位名用 base.pick() 以「包含」比對，降低改版造成的破壞。
"""
from __future__ import annotations

import pandas as pd

from .base import DataUnavailable, Sourced, http_get, pick, to_float

BASE = "https://openapi.twse.com.tw/v1"


def _get(path: str) -> list[dict]:
    try:
        data = http_get(f"{BASE}{path}").json()
    except Exception as exc:  # 連線、HTTP 錯誤或非 JSON
        raise DataUnavailable(f"TWSE OpenAPI {path} 取得失敗：{exc}") from exc
    if not isinstance(data, list) or not data:
        raise DataUnavailable(f"TWSE OpenAPI {path} 回傳空資料")
    return data


def _roc_date(text: str | None) -> str | None:
    """'1141104' → '2025-11-04'（民國年）。其他格式原樣回傳。"""
    if not text:
        return None
    text = str(text).strip()
    if text.isdigit() and len(text) in (6, 7):
        year = int(text[:-4]) + 1911
        return f"{year}-{text[-4:-2]}-{text[-2:]}"
    return text


def parse_daily_quotes(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        code = str(pick(r, "Code", "證券代號") or "").strip()
        if not code:
            continue
        out.append({
            "code": code,
            "name": str(pick(r, "Name", "證券名稱") or "").strip(),
            "open": to_float(pick(r, "OpeningPrice", "開盤價")),
            "high": to_float(pick(r, "HighestPrice", "最高價")),
            "low": to_float(pick(r, "LowestPrice", "最低價")),
            "close": to_float(pick(r, "ClosingPrice", "收盤價")),
            "change": to_float(pick(r, "Change", "漲跌價差")),
            "volume": to_float(pick(r, "TradeVolume", "成交股數")),
            "turnover": to_float(pick(r, "TradeValue", "成交金額")),
            "date": _roc_date(pick(r, "Date", "日期")),
        })
    return pd.DataFrame(out)


def parse_valuation(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        code = str(pick(r, "Code", "證券代號") or "").strip()
        if not code:
            continue
        out.append({
            "code": code,
            "pe": to_float(pick(r, "PEratio", "本益比")),
            "pb": to_float(pick(r, "PBratio", "股價淨值比")),
            "dividend_yield": to_float(pick(r, "DividendYield", "殖利率")),
            "valuation_date": _roc_date(pick(r, "Date", "日期")),
        })
    return pd.DataFrame(out)


def parse_monthly_revenue(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        code = str(pick(r, "公司代號") or "").strip()
        if not code:
            continue
        out.append({
            "code": code,
            "name": str(pick(r, "公司名稱") or "").strip(),
            "industry": str(pick(r, "產業別") or "").strip(),
            "revenue_month": str(pick(r, "資料年月") or "").strip(),
            "revenue": to_float(pick(r, "當月營收")),
            "revenue_mom": to_float(pick(r, "上月比較增減")),
            "revenue_yoy": to_float(pick(r, "去年同月增減")),
            "revenue_ytd_yoy": to_float(pick(r, "前期比較增減")),
        })
    return pd.DataFrame(out)


def parse_income_statement(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        code = str(pick(r, "公司代號") or "").strip()
        if not code:
            continue
        out.append({
            "code": code,
            "year": to_float(pick(r, "年度")),
            "quarter": to_float(pick(r, "季別")),
            "revenue": to_float(pick(r, "營業收入", exclude=("成本",))),
            "gross_profit": to_float(pick(r, "營業毛利")),
            "operating_income": to_float(pick(r, "營業利益")),
            "net_income": to_float(pick(r, "本期淨利", "本期稅後淨利", exclude=("歸屬於非控制", "其他綜合"))),
            "eps": to_float(pick(r, "基本每股盈餘", "每股盈餘")),
        })
    return pd.DataFrame(out)


def parse_balance_sheet(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        code = str(pick(r, "公司代號") or "").strip()
        if not code:
            continue
        out.append({
            "code": code,
            "total_assets": to_float(pick(r, "資產總額", "資產總計")),
            "total_liabilities": to_float(pick(r, "負債總額", "負債總計")),
            "equity": to_float(pick(r, "權益總額", "權益總計", exclude=("非控制",))),
        })
    return pd.DataFrame(out)


def parse_code_list(rows: list[dict]) -> set[str]:
    codes = set()
    for r in rows:
        code = pick(r, "Code", "證券代號", "代號")
        if code:
            codes.add(str(code).strip())
    return codes


def daily_quotes() -> Sourced:
    df = parse_daily_quotes(_get("/exchangeReport/STOCK_DAY_ALL"))
    as_of = df["date"].dropna().max() if "date" in df and df["date"].notna().any() else None
    return Sourced(df, "TWSE OpenAPI STOCK_DAY_ALL", as_of, f"{BASE}/exchangeReport/STOCK_DAY_ALL")


def valuation() -> Sourced:
    df = parse_valuation(_get("/exchangeReport/BWIBBU_ALL"))
    as_of = df["valuation_date"].dropna().max() if df["valuation_date"].notna().any() else None
    return Sourced(df, "TWSE OpenAPI BWIBBU_ALL", as_of, f"{BASE}/exchangeReport/BWIBBU_ALL")


def monthly_revenue() -> Sourced:
    df = parse_monthly_revenue(_get("/opendata/t187ap05_L"))
    as_of = df["revenue_month"].max() if len(df) else None
    return Sourced(df, "TWSE OpenAPI t187ap05_L（上市公司月營收）", as_of, f"{BASE}/opendata/t187ap05_L")


def latest_financials() -> Sourced:
    inc = parse_income_statement(_get("/opendata/t187ap06_L_ci"))
    try:
        bal = parse_balance_sheet(_get("/opendata/t187ap07_L_ci"))
        inc = inc.merge(bal, on="code", how="left")
    except DataUnavailable:
        pass
    as_of = None
    if len(inc) and inc["year"].notna().any():
        y, q = inc["year"].max(), inc.loc[inc["year"] == inc["year"].max(), "quarter"].max()
        as_of = f"民國{int(y)}年Q{int(q)}"
    return Sourced(inc, "TWSE OpenAPI t187ap06_L_ci / t187ap07_L_ci（一般業最近一季）", as_of)


def warning_lists() -> Sourced:
    """處置股票與注意股票。取不到時回傳 None 欄位而非空集合，避免被誤判為「沒有警示」。"""
    result: dict[str, set[str] | None] = {"disposition": None, "attention": None}
    notes = []
    for key, path in (("disposition", "/announcement/punish"), ("attention", "/announcement/notice")):
        try:
            result[key] = parse_code_list(_get(path))
        except DataUnavailable as exc:
            notes.append(str(exc))
    return Sourced(result, "TWSE OpenAPI 公告（處置／注意股票）", notes=notes)
