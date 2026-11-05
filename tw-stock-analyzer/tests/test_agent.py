import json
from types import SimpleNamespace

from app import agent
from app.config import settings


class FakeRunner:
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def __iter__(self):
        tool = SimpleNamespace(type="tool_use", name="screen_stocks", input={"preset": "auto"})
        yield SimpleNamespace(content=[tool], stop_reason="tool_use", model="m")
        text = SimpleNamespace(type="text", text="結論：……以上為量化研究資訊，非投資建議")
        yield SimpleNamespace(content=[text], stop_reason="end_turn", model="m")


def test_agent_loop_records_tool_calls(monkeypatch):
    captured = {}

    def tool_runner(**kw):
        captured.update(kw)
        return FakeRunner(kw)

    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(tool_runner=tool_runner)))
    monkeypatch.setattr(settings, "anthropic_key", "test")
    monkeypatch.setattr(agent, "_client", lambda: fake)
    res = agent.ask("今天推薦什麼？", [{"role": "user", "content": "嗨"}, {"role": "assistant", "content": "你好"}])
    assert res["answer"].startswith("結論")
    assert res["tool_calls"] == [{"tool": "screen_stocks", "input": {"preset": "auto"}}]
    assert captured["thinking"] == {"type": "adaptive"}
    assert captured["fallbacks"] == "default"
    assert captured["messages"][-1]["content"] == "今天推薦什麼？"
    assert "不可以用訓練資料或記憶補數字" in captured["system"]
    names = {t.name for t in captured["tools"]}
    assert {"screen_stocks", "analyze_stock", "backtest_factor_strategy"} <= names


def test_tools_return_errors_as_data():
    tools = {t.name: t for t in agent._tools()}
    out = json.loads(tools["backtest_stock_strategy"].call({"code": "2330", "strategy": "nope"}))
    assert "error" in out
