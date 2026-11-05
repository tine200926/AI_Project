from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_and_index():
    assert client.get("/api/health").json()["mode"] == "demo"
    assert "台股量化分析" in client.get("/").text


def test_overview_and_screen():
    o = client.get("/api/overview").json()
    assert o["regime"]["preset"] in o["presets"]
    r = client.get("/api/screen", params={"preset": "dividend", "top_n": 5}).json()
    assert len(r["picks"]) == 5
    assert r["sources"]["is_demo"] is True
    for p in r["picks"]:
        assert p["risk"]["level"] in ("低", "中", "高", "極高")
        assert p["reasons"]
    assert {"2609", "2615"} <= {x["code"] for x in r["excluded"]}  # 模擬的處置／注意股被排除


def test_stock_endpoint():
    s = client.get("/api/stock/2330").json()
    for key in ("technical", "fundamentals", "risk", "sentiment", "verdict", "chart"):
        assert key in s
    assert s["sentiment"]["counts"]["filtered"] >= 0
    assert s["risk"]["plan"]["sizing"]["weight_pct"] <= 10


def test_backtests():
    r = client.post("/api/backtest/factor", json={"preset": "momentum", "years": 3, "top_n": 8}).json()
    assert r["metrics"]["cagr_pct"] is not None and r["curve"]
    s = client.post("/api/backtest/strategy", json={"code": "2330", "strategy": "breakout", "years": 3}).json()
    assert "trades" in s["metrics"]


def test_bad_inputs():
    assert client.get("/api/screen", params={"preset": "nope"}).status_code == 400
    assert client.post("/api/backtest/strategy", json={"code": "2330", "strategy": "nope"}).status_code == 400
    assert client.post("/api/backtest/factor", json={"preset": "nope"}).status_code == 400


def test_agent_disabled_without_key():
    r = client.post("/api/agent", json={"question": "hi"}).json()
    assert "ANTHROPIC_API_KEY" in r["error"]
