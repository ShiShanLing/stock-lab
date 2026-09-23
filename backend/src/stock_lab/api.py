from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .backtest import run_backtest
from .models import (
    BacktestRequest,
    BacktestResult,
    NaturalLanguageRequest,
    NaturalLanguageResult,
    ScreenRequest,
    ScreenResult,
    Strategy,
    StrategyCreate,
)
from .repository import Repository, get_repository
from .screening import run_screen
from .skill import PRESETS, parse_natural_language


def create_api(repository: Repository | None = None) -> FastAPI:
    app = FastAPI(
        title="Stock Lab API",
        version="0.1.0",
        description="个人条件选股和回测实验 API；不连接真实交易。",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5176", "http://localhost:5176"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def repo() -> Repository:
        return repository or get_repository()

    @app.get("/health")
    @app.get("/api/health")
    def health(active_repository: Repository = Depends(repo)) -> dict[str, object]:
        return {
            "status": "ok",
            "provider": "demo",
            "latest_trade_date": active_repository.latest_trade_date(),
        }

    @app.get("/api/presets")
    def presets() -> dict[str, object]:
        return {"items": [{"name": name, **value} for name, value in PRESETS.items()]}

    @app.post("/api/skill/parse", response_model=NaturalLanguageResult)
    def parse_skill(request: NaturalLanguageRequest) -> NaturalLanguageResult:
        return parse_natural_language(request.text)

    @app.post("/api/screen", response_model=ScreenResult)
    def screen(
        request: ScreenRequest,
        active_repository: Repository = Depends(repo),
    ) -> ScreenResult:
        return run_screen(active_repository, request.rule, request.trade_date)

    @app.get("/api/strategies", response_model=list[Strategy])
    def strategies(active_repository: Repository = Depends(repo)) -> list[Strategy]:
        return active_repository.list_strategies()

    @app.post("/api/strategies", response_model=Strategy, status_code=201)
    def create_strategy(
        request: StrategyCreate,
        active_repository: Repository = Depends(repo),
    ) -> Strategy:
        return active_repository.save_strategy(request.name, request.description, request.rule)

    @app.post("/api/backtests", response_model=BacktestResult, status_code=201)
    def backtest(
        request: BacktestRequest,
        active_repository: Repository = Depends(repo),
    ) -> BacktestResult:
        try:
            return run_backtest(active_repository, request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


app = create_api()


def run() -> None:
    import uvicorn

    uvicorn.run("stock_lab.api:app", host="127.0.0.1", port=8010, reload=False)
