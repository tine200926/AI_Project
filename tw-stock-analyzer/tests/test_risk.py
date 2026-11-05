import numpy as np
import pandas as pd

from app.analysis import risk


def _px(vol, n=300, turnover=1e9, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, vol, n)))
    idx = pd.bdate_range("2025-01-01", periods=n)
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": turnover / close, "turnover": turnover}, index=idx)


def test_position_size_respects_risk_budget_and_cap():
    p = risk.position_size(1_000_000, entry=100, stop=95, risk_pct=1)
    assert p["max_loss"] <= 10_000 and p["weight_pct"] <= 10
    p2 = risk.position_size(1_000_000, entry=100, stop=99.9, risk_pct=1)
    assert p2["binding"] == "單一部位上限" and p2["shares"] == 1000
    assert risk.position_size(1e6, 100, 101)["shares"] == 0


def test_high_vol_illiquid_disposition_is_extreme():
    df = _px(0.05, turnover=5e6)
    res = risk.assess(df, code="9999", warnings={"disposition": {"9999"}, "attention": set()})
    assert res["level"] == "極高"
    texts = " ".join(f["text"] for f in res["flags"])
    assert "處置" in texts and "流動性極低" in texts


def test_calm_liquid_stock_is_low():
    res = risk.assess(_px(0.005), code="1234", warnings={"disposition": set(), "attention": set()})
    assert res["level"] == "低"
    assert res["plan"]["stop_loss"] < res["plan"]["entry_ref"]


def test_missing_warning_list_is_flagged_not_assumed_safe():
    res = risk.assess(_px(0.005), code="1234", warnings=None)
    assert any("未取得" in f["text"] for f in res["flags"])


def test_max_drawdown():
    s = pd.Series([100, 120, 60, 90])
    assert risk.max_drawdown(s) == -0.5
