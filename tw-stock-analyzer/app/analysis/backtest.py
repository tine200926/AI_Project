"""策略回測：多因子月調倉回測，與單一個股的技術面策略回測。

真實性設定：
- 訊號在 t 日收盤後產生，t+1 日成交（多因子：t+1 收盤；個股：t+1 開盤）。
- 交易成本：手續費 0.1425%（可設折扣）＋賣出證交稅 0.3%（ETF 0.1%）＋滑價。
- 月營收依公布期限延後使用；回測期間拿不到歷史值的因子（ROE、法人）會被剔除並註明。
- 報告樣本內（前 70%）與樣本外（後 30%）績效，差距過大會提示可能過度擬合。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .factors import PRESETS, price_factors, rank_scores, revenue_yoy_at
from .indicators import atr, macd, rsi, sma


@dataclass
class Costs:
    fee: float = 0.001425
    fee_discount: float = 1.0
    tax: float = 0.003
    etf_tax: float = 0.001
    slippage: float = 0.001

    def buy(self) -> float:
        return self.fee * self.fee_discount + self.slippage

    def sell(self, code: str = "") -> float:
        tax = self.etf_tax if code.startswith("00") else self.tax
        return self.fee * self.fee_discount + tax + self.slippage


def _r(x, nd=4):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), nd)


def metrics(equity: pd.Series, rf: float = 0.015) -> dict:
    equity = equity.dropna()
    if len(equity) < 2:
        return {}
    rets = equity.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365)
    total = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    vol = rets.std() * np.sqrt(252)
    downside = rets[rets < 0].std() * np.sqrt(252)
    mdd = float((equity / equity.cummax() - 1).min())
    monthly = equity.resample("ME").last().pct_change().dropna()
    return {
        "total_return_pct": _r(total * 100, 2),
        "cagr_pct": _r(cagr * 100, 2),
        "volatility_pct": _r(vol * 100, 2),
        "sharpe": _r((cagr - rf) / vol, 2) if vol else None,
        "sortino": _r((cagr - rf) / downside, 2) if downside else None,
        "max_drawdown_pct": _r(mdd * 100, 2),
        "calmar": _r(cagr / abs(mdd), 2) if mdd else None,
        "monthly_win_rate_pct": _r((monthly > 0).mean() * 100, 1) if len(monthly) else None,
        "start": str(equity.index[0].date()),
        "end": str(equity.index[-1].date()),
        "years": _r(years, 2),
    }


def _split_metrics(equity: pd.Series, split: float = 0.7) -> dict:
    cut = equity.index[int(len(equity) * split)]
    ins, oos = metrics(equity.loc[:cut]), metrics(equity.loc[cut:])
    warn = None
    if ins.get("sharpe") is not None and oos.get("sharpe") is not None and ins["sharpe"] > 0.5:
        if oos["sharpe"] < ins["sharpe"] * 0.4:
            warn = f"樣本外 Sharpe（{oos['sharpe']}）遠低於樣本內（{ins['sharpe']}），策略可能過度擬合或市場結構已改變"
    return {"in_sample": ins, "out_of_sample": oos, "split_date": str(cut.date()), "warning": warn}


def _curve(equity: pd.Series, bench: pd.Series | None, step: int = 1) -> list[dict]:
    e = equity / equity.iloc[0]
    b = None
    if bench is not None:
        b = bench.reindex(e.index).ffill()
        b = b / b.dropna().iloc[0] if b.notna().any() else None
    out = []
    for i, (d, v) in enumerate(e.items()):
        if i % step and i != len(e) - 1:
            continue
        row = {"time": str(d.date()), "strategy": round(float(v), 5)}
        if b is not None and pd.notna(b.loc[d]):
            row["benchmark"] = round(float(b.loc[d]), 5)
        out.append(row)
    return out


# ----------------------------------------------------------------- 多因子回測

def factor_backtest(
    panel: dict[str, pd.DataFrame],
    weights: dict[str, float] | str,
    *,
    top_n: int = 10,
    start: str | None = None,
    end: str | None = None,
    costs: Costs | None = None,
    bench: pd.Series | None = None,
    revenue: dict[str, pd.DataFrame] | None = None,
    valuation: dict[str, pd.DataFrame] | None = None,
    min_turnover: float = 5e7,
) -> dict:
    costs = costs or Costs()
    preset_name = None
    if isinstance(weights, str):
        if weights not in PRESETS:
            raise ValueError(f"未知策略：{weights}（可用：{', '.join(PRESETS)}）")
        preset_name = PRESETS[weights]["name"]
        weights = PRESETS[weights]["weights"]
    close = pd.DataFrame({c: df["close"] for c, df in panel.items()}).sort_index()
    turnover = pd.DataFrame({c: df["turnover"] for c, df in panel.items() if "turnover" in df}).reindex(close.index)
    rets = close.pct_change(fill_method=None).fillna(0.0)

    first_ok = close.index[min(252, len(close) - 1)]
    lo = max(pd.Timestamp(start), first_ok) if start else first_ok
    hi = pd.Timestamp(end) if end else close.index[-1]
    days = close.index[(close.index >= lo) & (close.index <= hi)]
    if len(days) < 40:
        raise ValueError("回測期間太短（需要至少一年暖身資料 + 兩個月回測期）")
    month_ends = pd.Series(days, index=days).groupby(days.to_period("M")).last().tolist()
    if month_ends[-1] == days[-1]:
        month_ends = month_ends[:-1]  # 最後一天的訊號沒有 t+1 可成交

    rebal_exec: dict[pd.Timestamp, tuple[pd.Timestamp, list[str], dict]] = {}
    history: list[dict] = []
    info_used: dict = {}
    pos = {d: i for i, d in enumerate(close.index)}
    for t in month_ends:
        feats = price_factors(close, t)
        if revenue:
            feats["rev_yoy"] = pd.Series({c: revenue_yoy_at(df, t) for c, df in revenue.items()}, dtype=float)
        if valuation:
            for col in ("pe", "pb", "dividend_yield"):
                feats[col] = pd.Series({c: (v.loc[:t, col].dropna().iloc[-1] if len(v.loc[:t, col].dropna()) else np.nan)
                                        for c, v in valuation.items()}, dtype=float)
        liq = turnover.loc[:t].tail(20).mean() if not turnover.empty else pd.Series(np.inf, index=close.columns)
        eligible = feats.index[feats["mom_6_1"].notna() & (liq.reindex(feats.index).fillna(0) >= min_turnover)]
        if len(eligible) < top_n:
            continue
        ranked, info_used = rank_scores(feats.loc[eligible], weights)
        picks = ranked.index[:top_n].tolist()
        i = pos[t]
        if i + 1 >= len(close.index):
            continue
        exec_day = close.index[i + 1]
        rebal_exec[exec_day] = (t, picks, {c: round(float(ranked.loc[c, "score"]), 1) for c in picks})

    if not rebal_exec:
        raise ValueError("沒有任何調倉日符合條件（股票數不足或流動性門檻過高）")

    sim_days = close.index[(close.index >= min(rebal_exec)) & (close.index <= hi)]
    w = pd.Series(0.0, index=close.columns)
    equity, eq = [], 1.0
    total_turnover, total_cost = 0.0, 0.0
    for d in sim_days:
        r = rets.loc[d]
        day_ret = float((w * r).sum())
        eq *= 1 + day_ret
        if day_ret != -1:
            w = w * (1 + r) / (1 + day_ret)
        if d in rebal_exec:
            signal_day, picks, scores = rebal_exec[d]
            target = pd.Series(0.0, index=close.columns)
            target[picks] = 1.0 / len(picks)
            diff = target - w
            buy_cost = diff.clip(lower=0).sum() * costs.buy()
            sell_cost = sum(-v * costs.sell(c) for c, v in diff.items() if v < 0)
            cost = buy_cost + sell_cost
            eq *= 1 - cost
            total_turnover += float(diff.abs().sum()) / 2
            total_cost += cost
            w = target
            history.append({"signal_date": str(signal_day.date()), "trade_date": str(d.date()),
                            "picks": picks, "scores": scores})
        equity.append((d, eq))
    equity_s = pd.Series(dict(equity))

    res = {
        "preset": preset_name,
        "weights": weights,
        "factors_used": info_used.get("used"),
        "factors_dropped": info_used.get("dropped"),
        "metrics": metrics(equity_s),
        "benchmark_metrics": metrics(bench.reindex(equity_s.index).ffill().dropna()) if bench is not None else None,
        "split": _split_metrics(equity_s),
        "rebalances": len(history),
        "avg_turnover_pct": _r(total_turnover / max(len(history), 1) * 100, 1),
        "total_cost_pct": _r(total_cost * 100, 2),
        "curve": _curve(equity_s, bench, step=max(1, len(equity_s) // 600)),
        "holdings": history[-12:],
        "warnings": [
            "選股池為「現在」的權值股清單，含存活者偏差：歷史上下市或掉出權值股的公司不在其中，績效會偏樂觀。",
            "沒有歷史的處置／注意股清單，回測期間無法排除當時被列管的股票（今日選股會排除）。",
            "過去績效不代表未來報酬。",
        ],
    }
    if res["factors_dropped"]:
        res["warnings"].append("回測期間無歷史資料而剔除的因子：" + "、".join(res["factors_dropped"]) +
                               "（今日選股仍會使用，兩者權重略有差異）")
    if res["split"]["warning"]:
        res["warnings"].append(res["split"]["warning"])
    if bench is not None and res["benchmark_metrics"]:
        bm = res["benchmark_metrics"]
        if res["metrics"].get("cagr_pct") is not None and bm.get("cagr_pct") is not None:
            res["excess_cagr_pct"] = _r(res["metrics"]["cagr_pct"] - bm["cagr_pct"], 2)
    return res


# ----------------------------------------------------------------- 個股策略回測

STRATEGIES = {
    "ma_cross": {"name": "均線交叉", "params": {"fast": 20, "slow": 60}},
    "macd": {"name": "MACD 柱狀體翻正", "params": {"fast": 12, "slow": 26, "signal": 9}},
    "rsi": {"name": "RSI 逆勢", "params": {"low": 30, "high": 70, "n": 14}},
    "breakout": {"name": "通道突破（海龜）", "params": {"entry": 55, "exit": 20}},
}


def _signals(df: pd.DataFrame, strategy: str, p: dict) -> tuple[pd.Series, pd.Series]:
    c = df["close"]
    if strategy == "ma_cross":
        up = sma(c, int(p["fast"])) > sma(c, int(p["slow"]))
        return up & ~up.shift(fill_value=False), ~up & up.shift(fill_value=False)
    if strategy == "macd":
        h = macd(c, int(p["fast"]), int(p["slow"]), int(p["signal"]))["hist"]
        return (h > 0) & (h.shift() <= 0), (h < 0) & (h.shift() >= 0)
    if strategy == "rsi":
        r = rsi(c, int(p["n"]))
        return (r < p["low"]) & (r.shift() >= p["low"]), r > p["high"]
    if strategy == "breakout":
        hi = df["high"].rolling(int(p["entry"])).max().shift()
        lo = df["low"].rolling(int(p["exit"])).min().shift()
        return c > hi, c < lo
    raise ValueError(f"未知策略：{strategy}")


def strategy_backtest(df: pd.DataFrame, strategy: str = "ma_cross", params: dict | None = None, *,
                      code: str = "", stop_atr: float | None = 2.5, costs: Costs | None = None,
                      bench: pd.Series | None = None) -> dict:
    costs = costs or Costs()
    if strategy not in STRATEGIES:
        raise ValueError(f"未知策略：{strategy}（可用：{', '.join(STRATEGIES)}）")
    p = {**STRATEGIES[strategy]["params"], **(params or {})}
    df = df.dropna(subset=["close"]).copy()
    if len(df) < 150:
        raise ValueError("歷史資料不足 150 天")
    entry_sig, exit_sig = _signals(df, strategy, p)
    a = atr(df)
    opens = df["open"].where(df["open"] > 0, df["close"])

    cash, shares, eq = 1.0, 0.0, []
    in_pos, pending = False, None
    trades: list[dict] = []
    stop = peak = entry_px = None
    entry_date = None
    idx = df.index
    for i, d in enumerate(idx):
        row = df.iloc[i]
        # 1) 執行前一天收盤產生的指令（今天開盤成交）
        if pending == "buy" and not in_pos:
            entry_px = float(opens.iloc[i])
            shares = cash * (1 - costs.buy()) / entry_px
            cash, in_pos, entry_date, peak = 0.0, True, d, entry_px
            stop = entry_px - stop_atr * float(a.iloc[i - 1]) if stop_atr and pd.notna(a.iloc[i - 1]) else None
        elif pending == "sell" and in_pos:
            px = float(opens.iloc[i])
            cash = shares * px * (1 - costs.sell(code))
            trades.append({"entry_date": str(entry_date.date()), "exit_date": str(d.date()),
                           "entry": _r(entry_px, 2), "exit": _r(px, 2), "reason": "出場訊號",
                           "return_pct": _r((cash / (shares * entry_px / (1 - costs.buy())) - 1) * 100, 2)})
            shares, in_pos = 0.0, False
        pending = None
        # 2) 盤中觸發移動停損
        if in_pos and stop is not None and row["low"] <= stop:
            px = min(float(opens.iloc[i]), stop)
            cash = shares * px * (1 - costs.sell(code))
            trades.append({"entry_date": str(entry_date.date()), "exit_date": str(d.date()),
                           "entry": _r(entry_px, 2), "exit": _r(px, 2), "reason": "ATR 停損",
                           "return_pct": _r((cash / (shares * entry_px / (1 - costs.buy())) - 1) * 100, 2)})
            shares, in_pos = 0.0, False
        # 3) 收盤：更新移動停損、產生明日指令
        if in_pos:
            peak = max(peak, float(row["close"]))
            if stop_atr and pd.notna(a.iloc[i]):
                stop = max(stop or -np.inf, peak - stop_atr * float(a.iloc[i]))
            if exit_sig.iloc[i]:
                pending = "sell"
        elif entry_sig.iloc[i] and i + 1 < len(idx):
            pending = "buy"
        eq.append(cash + shares * float(row["close"]))

    equity = pd.Series(eq, index=idx)
    if in_pos:
        trades.append({"entry_date": str(entry_date.date()), "exit_date": None, "entry": _r(entry_px, 2),
                       "exit": None, "reason": "持有中",
                       "return_pct": _r((float(df["close"].iloc[-1]) / entry_px - 1) * 100, 2)})
    closed = [t for t in trades if t["exit_date"]]
    wins = [t for t in closed if t["return_pct"] > 0]
    gross_win = sum(t["return_pct"] for t in wins)
    gross_loss = -sum(t["return_pct"] for t in closed if t["return_pct"] <= 0)
    exposure = float((equity.diff().fillna(0) != 0).mean())
    bh = df["close"] / df["close"].iloc[0]
    warnings = ["過去績效不代表未來報酬。"]
    if len(closed) < 10:
        warnings.append(f"已完成交易只有 {len(closed)} 筆，統計上不具代表性。")
    split = _split_metrics(equity)
    if split["warning"]:
        warnings.append(split["warning"])
    return {
        "strategy": STRATEGIES[strategy]["name"],
        "params": p,
        "stop_atr": stop_atr,
        "metrics": {
            **metrics(equity),
            "trades": len(closed),
            "win_rate_pct": _r(len(wins) / len(closed) * 100, 1) if closed else None,
            "profit_factor": _r(gross_win / gross_loss, 2) if gross_loss else None,
            "avg_trade_pct": _r(np.mean([t["return_pct"] for t in closed]), 2) if closed else None,
            "exposure_pct": _r(exposure * 100, 1),
        },
        "buy_hold_metrics": metrics(bh),
        "split": split,
        "trades": trades[-30:],
        "curve": _curve(equity, bh if bench is None else bench, step=max(1, len(equity) // 600)),
        "warnings": warnings,
    }
