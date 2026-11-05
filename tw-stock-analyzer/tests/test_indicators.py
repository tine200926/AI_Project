import numpy as np
import pandas as pd

from app.analysis import indicators as ind
from app.analysis import technical


def _df(close):
    close = pd.Series(close, index=pd.bdate_range("2024-01-01", periods=len(close)), dtype=float)
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": 1e6, "turnover": close * 1e6})


def test_rsi_bounds_and_extremes():
    up = _df(np.linspace(10, 50, 60))
    assert ind.rsi(up["close"]).iloc[-1] == 100
    down = _df(np.linspace(50, 10, 60))
    assert ind.rsi(down["close"]).iloc[-1] < 1
    rnd = _df(100 + np.cumsum(np.random.default_rng(0).normal(0, 1, 300)))
    r = ind.rsi(rnd["close"]).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_sma_ema_macd_shapes():
    s = pd.Series(np.arange(1, 101, dtype=float))
    assert ind.sma(s, 5).iloc[-1] == 98
    assert ind.sma(s, 5).isna().sum() == 4
    m = ind.macd(s)
    assert (m["dif"].dropna() > 0).all()  # 單調上升，快線恆在慢線之上


def test_kd_range():
    rnd = _df(100 + np.cumsum(np.random.default_rng(1).normal(0, 1, 200)))
    kd = ind.kd(rnd).dropna()
    assert kd["k"].between(0, 100).all() and kd["d"].between(0, 100).all()


def test_technical_uptrend_is_bullish():
    res = technical.analyze(_df(np.linspace(50, 120, 300)))
    assert res["label"] == "偏多" and res["score"] > 30
    names = [s["name"] for s in res["signals"]]
    assert "均線多頭排列" in names


def test_technical_downtrend_is_bearish():
    res = technical.analyze(_df(np.linspace(120, 50, 300)))
    assert res["label"] == "偏空"


def test_technical_needs_history():
    assert "error" in technical.analyze(_df(np.linspace(1, 2, 30)))
