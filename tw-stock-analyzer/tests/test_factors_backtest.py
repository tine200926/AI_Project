import numpy as np
import pandas as pd
import pytest

from app.analysis import backtest as bt
from app.analysis.factors import market_regime, price_factors, rank_scores, revenue_yoy_at


def _panel(n_stocks=12, n_days=700, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-01", periods=n_days)
    panel = {}
    for i in range(n_stocks):
        drift = (i - n_stocks / 2) * 0.0003
        close = 50 * np.exp(np.cumsum(rng.normal(drift, 0.015, n_days)))
        panel[f"{1000 + i}"] = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                                             "close": close, "volume": 1e7, "turnover": close * 1e7}, index=idx)
    return panel


def test_rank_scores_direction_and_missing():
    feats = pd.DataFrame({"mom_6_1": [0.3, 0.1, -0.2, np.nan], "low_vol": [0.2, 0.4, 0.3, 0.1]},
                         index=list("ABCD"))
    ranked, info = rank_scores(feats, {"mom_6_1": 0.5, "low_vol": 0.5, "roe": 0.2})
    assert info["dropped"] == ["roe"]
    assert ranked.loc["A", "mom_6_1"] > ranked.loc["C", "mom_6_1"]
    assert ranked.loc["D", "low_vol"] > ranked.loc["B", "low_vol"]  # 低波動排前面
    assert ranked.loc["D", "mom_6_1"] == 0.5                         # 缺值給中性


def test_pe_loss_ranks_worst():
    feats = pd.DataFrame({"pe": [10, 20, None, -5]}, index=list("ABCD"))
    ranked, _ = rank_scores(feats, {"pe": 1})
    assert ranked.index[0] == "A"
    assert ranked.loc["C", "pe"] <= ranked.loc["B", "pe"]


def test_price_factors_no_lookahead():
    panel = _panel()
    close = pd.DataFrame({c: d["close"] for c, d in panel.items()})
    t = close.index[400]
    f1 = price_factors(close, t)
    changed = close.copy()
    changed.iloc[401:] *= 3  # 竄改未來資料
    f2 = price_factors(changed, t)
    pd.testing.assert_frame_equal(f1, f2)


def test_revenue_respects_publication_lag():
    rev = pd.DataFrame({"year": [2024, 2025], "month": [9, 9], "revenue": [100.0, 150.0],
                        "available_from": pd.to_datetime(["2024-10-10", "2025-10-10"])})
    assert revenue_yoy_at(rev, pd.Timestamp("2025-10-09")) is None
    assert revenue_yoy_at(rev, pd.Timestamp("2025-10-10")) == pytest.approx(50.0)


def test_factor_backtest_runs_and_costs_reduce_return():
    panel = _panel()
    free = bt.factor_backtest(panel, "momentum", top_n=4, costs=bt.Costs(fee=0, tax=0, slippage=0), min_turnover=0)
    paid = bt.factor_backtest(panel, "momentum", top_n=4, min_turnover=0)
    assert paid["metrics"]["total_return_pct"] < free["metrics"]["total_return_pct"]
    assert paid["rebalances"] > 10
    assert "rev_yoy" in paid["factors_dropped"]  # 沒給營收歷史就剔除並註明
    assert any("存活者偏差" in w for w in paid["warnings"])
    for h in paid["holdings"]:
        assert h["trade_date"] > h["signal_date"]  # 次日成交


def test_strategy_backtest_trades_next_day():
    panel = _panel(1, 600, seed=3)
    df = list(panel.values())[0]
    res = bt.strategy_backtest(df, "ma_cross", {"fast": 10, "slow": 30}, stop_atr=2)
    assert res["metrics"]["trades"] >= 1
    entry, exit_ = bt._signals(df, "ma_cross", {"fast": 10, "slow": 30})
    first_signal = entry[entry].index[0]
    assert res["trades"][0]["entry_date"] > str(first_signal.date()) or len(res["trades"]) > 30


def test_metrics_known_values():
    eq = pd.Series([1.0, 1.1, 0.99, 1.21], index=pd.bdate_range("2025-01-01", periods=4))
    m = bt.metrics(eq)
    assert m["total_return_pct"] == 21.0
    assert m["max_drawdown_pct"] == -10.0


def test_market_regime_bull_bear():
    up = pd.Series(np.linspace(50, 100, 300))
    down = pd.Series(np.linspace(100, 50, 300))
    assert market_regime(up)["preset"] == "momentum"
    assert market_regime(down)["preset"] == "defensive"
