"""FastAPI 入口：`uvicorn app.main:app --reload` 後開 http://127.0.0.1:8000"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, service
from .config import ROOT, settings
from .data.base import DataUnavailable
from .data.universe import parse_universe

app = FastAPI(title="台股量化分析", version="1.0.0")
STATIC = ROOT / "static"


def _run(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except DataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/health")
def health():
    return {"ok": True, "mode": settings.data_mode, "ai_enabled": settings.ai_enabled}


@app.get("/api/overview")
def overview():
    return _run(service.overview)


@app.get("/api/screen")
def screen(preset: str = "auto", top_n: int = Query(10, ge=1, le=30), universe: str | None = None,
           min_turnover: float = Query(5e7, ge=0)):
    return _run(service.screen, preset, parse_universe(universe) if universe else None, top_n, min_turnover)


@app.get("/api/stock/{code}")
def stock(code: str, capital: float = Query(1_000_000, gt=0), risk_pct: float = Query(1.0, gt=0, le=10),
          atr_mult: float = Query(2.0, gt=0, le=6)):
    return _run(service.stock, code, capital, risk_pct, atr_mult)


@app.get("/api/news/market")
def market_news(days: int = Query(1, ge=1, le=7), ai: bool = False):
    res = _run(service.market_news, days)
    if ai:
        res["ai"] = agent.news_digest(res)
    return res


class FactorBacktestIn(BaseModel):
    preset: str = "momentum"
    universe: str | None = None
    years: float = Field(5, ge=1, le=10)
    top_n: int = Field(10, ge=3, le=30)
    fee_discount: float = Field(1.0, gt=0, le=1)
    use_revenue: bool = True
    use_valuation: bool = False
    min_turnover: float = Field(5e7, ge=0)


@app.post("/api/backtest/factor")
def backtest_factor(body: FactorBacktestIn):
    return _run(service.backtest_factor, body.preset, parse_universe(body.universe) if body.universe else None,
                body.years, body.top_n, body.fee_discount, body.use_revenue, body.use_valuation, body.min_turnover)


class StrategyBacktestIn(BaseModel):
    code: str
    strategy: str = "ma_cross"
    params: dict[str, float] | None = None
    years: float = Field(5, ge=1, le=10)
    stop_atr: float | None = Field(2.5, ge=0, le=10)
    fee_discount: float = Field(1.0, gt=0, le=1)


@app.post("/api/backtest/strategy")
def backtest_strategy(body: StrategyBacktestIn):
    return _run(service.backtest_strategy, body.code.strip().upper(), body.strategy, body.params, body.years,
                body.stop_atr or None, body.fee_discount)


class AskIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    history: list[dict] | None = None


@app.post("/api/agent")
def ask(body: AskIn):
    return agent.ask(body.question, body.history)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
