"""基本面與財報摘要：所有文字都由實際數字以規則產生，不經語言模型、不補猜。"""
from __future__ import annotations

import math

import pandas as pd


def _r(x, nd: int = 2):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def _pct(a, b):
    if a is None or b is None or b == 0 or pd.isna(a) or pd.isna(b):
        return None
    return (a / b - 1) * 100


def revenue_metrics(rev: pd.DataFrame | None) -> dict:
    if rev is None or rev.empty:
        return {}
    rev = rev.sort_values(["year", "month"]).reset_index(drop=True)
    rev["key"] = rev["year"] * 12 + rev["month"]
    by_key = rev.set_index("key")["revenue"]
    rev["yoy"] = [(_pct(v, by_key.get(k - 12))) for k, v in zip(rev["key"], rev["revenue"])]
    rev["mom"] = rev["revenue"].pct_change() * 100
    last = rev.iloc[-1]
    recent = rev.tail(3)
    streak = 0
    for v in reversed(rev["yoy"].tolist()):
        if v is not None and v > 0:
            streak += 1
        else:
            break
    hist12 = rev.tail(13).head(12)["revenue"]
    return {
        "month": f"{int(last['year'])}-{int(last['month']):02d}",
        "revenue": _r(last["revenue"], 0),
        "yoy": _r(last["yoy"]),
        "mom": _r(last["mom"]),
        "yoy_3m_avg": _r(pd.Series(recent["yoy"], dtype=float).mean()),
        "yoy_positive_streak": streak,
        "is_12m_high": bool(len(hist12) == 12 and last["revenue"] > hist12.max()),
        "series": [{"month": f"{int(r.year)}-{int(r.month):02d}", "revenue": _r(r.revenue, 0), "yoy": _r(r.yoy)}
                   for r in rev.tail(24).itertuples()],
    }


def financial_metrics(fin: pd.DataFrame | None) -> dict:
    if fin is None or fin.empty:
        return {}
    fin = fin.sort_index()
    last = fin.iloc[-1]
    cumulative = bool(fin.get("cumulative", pd.Series([False])).iloc[-1]) if "cumulative" in fin else False
    out: dict = {"cumulative": cumulative}
    if hasattr(fin.index[-1], "year"):
        q = (fin.index[-1].month - 1) // 3 + 1
        out["period"] = f"{fin.index[-1].year}Q{q}"
    rev = last.get("revenue")
    for col, name in (("gross_profit", "gross_margin"), ("operating_income", "operating_margin"),
                      ("net_income", "net_margin")):
        if col in fin and rev:
            out[name] = _r(last[col] / rev * 100)
    if "eps" in fin:
        out["eps_latest"] = _r(last["eps"])
        if not cumulative and len(fin) >= 4:
            out["eps_ttm"] = _r(fin["eps"].tail(4).sum())
        if not cumulative and len(fin) >= 8:
            out["eps_ttm_prev"] = _r(fin["eps"].iloc[-8:-4].sum())
            out["eps_ttm_growth"] = _r(_pct(out["eps_ttm"], out["eps_ttm_prev"])) if out["eps_ttm_prev"] and out["eps_ttm_prev"] > 0 else None
    if {"total_liabilities", "total_assets"} <= set(fin.columns) and last.get("total_assets"):
        out["debt_ratio"] = _r(last["total_liabilities"] / last["total_assets"] * 100)
    if "equity" in fin and "net_income" in fin and not cumulative and len(fin) >= 4:
        eq = fin["equity"].tail(5).mean()
        if eq and eq > 0:
            out["roe_ttm"] = _r(fin["net_income"].tail(4).sum() / eq * 100)
    if not cumulative and len(fin) >= 5 and "gross_profit" in fin and fin["revenue"].iloc[-5]:
        gm_prev = fin["gross_profit"].iloc[-5] / fin["revenue"].iloc[-5] * 100
        out["gross_margin_yoy_change"] = _r(out.get("gross_margin", 0) - gm_prev) if "gross_margin" in out else None
        out["revenue_q_yoy"] = _r(_pct(rev, fin["revenue"].iloc[-5]))
    out["quarters"] = [
        {"period": f"{d.year}Q{(d.month - 1) // 3 + 1}" if hasattr(d, "year") else str(d),
         "revenue": _r(r.get("revenue"), 0), "eps": _r(r.get("eps")),
         "gross_margin": _r(r["gross_profit"] / r["revenue"] * 100) if r.get("revenue") and pd.notna(r.get("gross_profit")) else None}
        for d, r in fin.tail(8).iterrows()
    ] if not cumulative else []
    return out


def summarize(fin: pd.DataFrame | None, rev: pd.DataFrame | None, valuation: dict | None) -> dict:
    fm = financial_metrics(fin)
    rm = revenue_metrics(rev)
    val = {k: _r((valuation or {}).get(k)) for k in ("pe", "pb", "dividend_yield")}
    highlights: list[dict] = []

    def hl(tone: str, text: str) -> None:
        highlights.append({"tone": tone, "text": text})

    if rm:
        if rm.get("yoy") is not None:
            tone = "pos" if rm["yoy"] > 10 else "neg" if rm["yoy"] < -10 else "neu"
            hl(tone, f"{rm['month']} 營收年增 {rm['yoy']:+.1f}%，月增 {rm['mom']:+.1f}%" if rm.get("mom") is not None
               else f"{rm['month']} 營收年增 {rm['yoy']:+.1f}%")
        if rm.get("yoy_positive_streak", 0) >= 3:
            hl("pos", f"營收連續 {rm['yoy_positive_streak']} 個月年成長")
        if rm.get("is_12m_high"):
            hl("pos", "單月營收創近 12 個月新高")
    if fm:
        if fm.get("gross_margin") is not None:
            chg = fm.get("gross_margin_yoy_change")
            hl("pos" if (chg or 0) > 1 else "neg" if (chg or 0) < -1 else "neu",
               f"{fm.get('period', '最近一季')} 毛利率 {fm['gross_margin']:.1f}%" +
               (f"（較去年同期 {chg:+.1f} 個百分點）" if chg is not None else ""))
        if fm.get("eps_ttm") is not None:
            g = fm.get("eps_ttm_growth")
            hl("neg" if fm["eps_ttm"] <= 0 else "pos" if (g or 0) > 10 else "neu",
               f"近四季 EPS {fm['eps_ttm']:.2f} 元" + (f"，年增 {g:+.1f}%" if g is not None else ""))
        if fm.get("roe_ttm") is not None:
            hl("pos" if fm["roe_ttm"] >= 15 else "neg" if fm["roe_ttm"] < 5 else "neu",
               f"近四季 ROE {fm['roe_ttm']:.1f}%")
        if fm.get("debt_ratio") is not None and fm["debt_ratio"] > 70:
            hl("neg", f"負債比 {fm['debt_ratio']:.1f}% 偏高（金融業屬正常結構）")
    if val.get("pe") is not None:
        hl("neg" if val["pe"] > 40 else "pos" if val["pe"] < 12 else "neu", f"本益比 {val['pe']:.1f} 倍")
    elif valuation is not None and "pe" in valuation:
        hl("neg", "本益比無法計算（近四季虧損或資料缺漏）")
    if val.get("dividend_yield"):
        hl("pos" if val["dividend_yield"] >= 5 else "neu", f"殖利率 {val['dividend_yield']:.2f}%")

    missing = [n for n, d in (("季財報", fm), ("月營收", rm)) if not d]
    return {"financials": fm, "revenue": rm, "valuation": val, "highlights": highlights, "missing": missing}
