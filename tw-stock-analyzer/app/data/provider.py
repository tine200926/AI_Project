"""統一的資料入口：依序嘗試多個來源、快取、並保留來源資訊。

live 模式下若所有來源都失敗，會丟出 DataUnavailable（附上每個來源的錯誤），
絕不會自動改用模擬資料，以免把假數字當成真數字。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

import pandas as pd

from .. import cache
from ..config import settings
from . import demo, finlab_src, finmind, news_rss, twse, yahoo
from .base import DataUnavailable, Sourced
from .universe import label


def _today() -> str:
    return date.today().isoformat()


def _first(name: str, attempts: list[tuple[str, Callable[[], Sourced]]]) -> Sourced:
    errors = []
    for label_, fn in attempts:
        try:
            result = fn()
            if errors:
                result.notes.append("前面的來源失敗：" + "；".join(errors))
            return result
        except DataUnavailable as exc:
            errors.append(f"{label_}: {exc}")
    raise DataUnavailable(f"{name}：所有資料源皆失敗 → " + "；".join(errors))


def _years_ago(years: float) -> str:
    return (date.today() - timedelta(days=int(365.25 * years))).isoformat()


def prices(code: str, years: float = 5) -> Sourced:
    if settings.demo:
        return demo.price_history(code, (demo.END - pd.DateOffset(days=int(365.25 * years))).strftime("%Y-%m-%d"))
    start = _years_ago(years)
    range_ = "10y" if years > 5 else "5y" if years > 2 else "2y"

    def load():
        attempts = []
        if finlab_src.available():
            attempts.append(("FinLab", lambda: finlab_src.price_history(code, start)))
        attempts.append(("Yahoo", lambda: yahoo.price_history(code, range_)))
        attempts.append(("FinMind", lambda: finmind.price_history(code, start)))
        res = _first(f"{code} 日線", attempts)
        res.data = res.data[res.data.index >= pd.Timestamp(start)]
        return res

    return cache.cached(f"prices:{code}:{years}:{_today()}", load)


def snapshot(codes: list[str] | None = None) -> Sourced:
    """全市場（或指定代號）最新一日：行情 + 本益比/淨值比/殖利率 + 最新月營收。"""
    if settings.demo:
        from .universe import DEFAULT_UNIVERSE
        return demo.snapshot(codes or list(DEFAULT_UNIVERSE))

    def load():
        quotes = twse.daily_quotes()
        df = quotes.data
        notes, sources = [], [quotes.source]
        for fn, how in ((twse.valuation, "valuation"), (twse.monthly_revenue, "revenue")):
            try:
                extra = fn()
                cols = [c for c in extra.data.columns if c != "name"]
                df = df.merge(extra.data[cols], on="code", how="left")
                sources.append(extra.source)
            except DataUnavailable as exc:
                notes.append(str(exc))
        return Sourced(df, " + ".join(sources), quotes.as_of, quotes.url, notes=notes)

    res = cache.cached(f"snapshot:{_today()}", load, ttl=3600)
    if codes:
        df = res.data[res.data["code"].isin(codes)].copy()
        missing = sorted(set(codes) - set(df["code"]))
        res = Sourced(df, res.source, res.as_of, res.url, notes=list(res.notes))
        if missing:
            res.notes.append("TWSE 上市資料中找不到：" + ", ".join(missing) + "（可能是上櫃股票或代號錯誤）")
    if "industry" not in res.data.columns:
        res.data["industry"] = res.data["code"].map(lambda c: label(c)[1])
    return res


def revenue_history(code: str, years: float = 6) -> Sourced:
    if settings.demo:
        return demo.monthly_revenue(code)
    return cache.cached(f"rev:{code}:{_today()}",
                        lambda: finmind.monthly_revenue(code, _years_ago(years)))


def financials(code: str) -> Sourced:
    if settings.demo:
        return demo.quarterly_financials(code)

    def from_twse() -> Sourced:
        latest = twse.latest_financials()
        row = latest.data[latest.data["code"] == code]
        if row.empty:
            raise DataUnavailable(f"TWSE 一般業財報找不到 {code}（金融／保險業格式不同）")
        df = row.drop(columns=["code", "year", "quarter"]).reset_index(drop=True)
        latest.data = df
        latest.notes.append("只有最近一季資料（損益為當年度累計數），無法計算年增率與近四季合計。")
        latest.data["cumulative"] = True
        return latest

    return cache.cached(f"fin:{code}:{_today()}", lambda: _first(f"{code} 財報", [
        ("FinMind", lambda: finmind.quarterly_financials(code, _years_ago(4))),
        ("TWSE", from_twse),
    ]))


def valuation_history(code: str, years: float = 5) -> Sourced:
    if settings.demo:
        raise DataUnavailable("模擬模式不提供本益比歷史")
    return cache.cached(f"per:{code}:{_today()}", lambda: finmind.valuation_history(code, _years_ago(years)))


def institutional(code: str) -> Sourced:
    if settings.demo:
        return demo.institutional(code)
    return cache.cached(f"inst:{code}:{_today()}", lambda: finmind.institutional(code, _years_ago(0.4)))


def news(code: str, name: str | None = None, days: int = 7) -> Sourced:
    if settings.demo:
        return demo.news(code)
    name = name or label(code)[0]

    def load():
        items, sources, notes = [], [], []
        for label_, fn in (
            ("FinMind", lambda: finmind.news(code, _years_ago(days / 365))),
            ("Google News", lambda: news_rss.search(f"{name} {code}" if name != code else code, days)),
        ):
            try:
                res = fn()
                items.extend(res.data)
                sources.append(res.source)
            except DataUnavailable as exc:
                notes.append(str(exc))
        if not items:
            raise DataUnavailable(f"{code} 新聞：" + "；".join(notes))
        return Sourced(items, " + ".join(sources), _today(), notes=notes)

    return cache.cached(f"news:{code}:{days}:{_today()}", load, ttl=1800)


def market_news(days: int = 1) -> Sourced:
    if settings.demo:
        return demo.market_news()

    def load():
        items, notes = [], []
        for q in ("台股", "台股 外資", "台積電 OR 電子股", "央行 OR 通膨 OR 美股 台股"):
            try:
                items.extend(news_rss.search(q, days).data)
            except DataUnavailable as exc:
                notes.append(str(exc))
        if not items:
            raise DataUnavailable("大盤新聞：" + "；".join(notes))
        return Sourced(items, "Google News RSS", _today(), notes=notes)

    return cache.cached(f"mnews:{days}:{_today()}", load, ttl=1800)


def warnings() -> Sourced:
    if settings.demo:
        return Sourced({"disposition": {"2609"}, "attention": {"2615"}}, demo.SOURCE, is_demo=True)
    return cache.cached(f"warn:{_today()}", twse.warning_lists, ttl=3600)
