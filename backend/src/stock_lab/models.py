from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Operator = Literal[">", ">=", "<", "<=", "=="]


class Condition(BaseModel):
    field: str
    operator: Operator
    value: float | bool
    label: str = ""


class SortRule(BaseModel):
    field: str = "change_20d"
    direction: Literal["asc", "desc"] = "desc"


class ScreenRule(BaseModel):
    markets: list[str] = Field(default_factory=lambda: ["sh", "sz"])
    exclude_st: bool = True
    exclude_suspended: bool = True
    min_listed_days: int = 365
    conditions: list[Condition] = Field(default_factory=list)
    sort: SortRule = Field(default_factory=SortRule)
    limit: int = Field(default=20, ge=1, le=100)


class StockSnapshot(BaseModel):
    code: str
    name: str
    market: str
    industry: str
    trade_date: str
    close: float
    pe: float
    pb: float
    market_cap_yi: float
    amount_yi: float
    turnover: float
    change_20d: float
    volume_ratio_5d: float
    volatility_20d: float
    ma5: float
    ma20: float
    ma60: float
    ma_bullish: bool
    listed_days: int
    is_st: bool
    suspended: bool
    reasons: list[str] = Field(default_factory=list)


class NaturalLanguageRequest(BaseModel):
    text: str = Field(min_length=2, max_length=1000)


class NaturalLanguageResult(BaseModel):
    summary: str
    rule: ScreenRule
    warnings: list[str] = Field(default_factory=list)


class ScreenRequest(BaseModel):
    rule: ScreenRule
    trade_date: str | None = None


class ScreenResult(BaseModel):
    trade_date: str
    total_universe: int
    matched_count: int
    stocks: list[StockSnapshot]


class StrategyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    rule: ScreenRule


class Strategy(BaseModel):
    id: int
    name: str
    description: str
    rule: ScreenRule
    created_at: str


class BacktestRequest(BaseModel):
    rule: ScreenRule
    start_date: str | None = None
    end_date: str | None = None
    initial_cash: float = Field(default=100_000, gt=0)
    rebalance_days: int = Field(default=20, ge=5, le=120)
    position_count: int = Field(default=5, ge=1, le=20)
    commission_rate: float = Field(default=0.0003, ge=0, le=0.02)
    slippage_rate: float = Field(default=0.0005, ge=0, le=0.02)


class BacktestMetric(BaseModel):
    total_return_pct: float
    annual_return_pct: float
    max_drawdown_pct: float
    benchmark_return_pct: float
    win_rate_pct: float
    periods: int


class EquityPoint(BaseModel):
    date: str
    equity: float
    benchmark: float


class BacktestPeriod(BaseModel):
    buy_date: str
    sell_date: str
    stocks: list[str]
    return_pct: float
    ending_equity: float


class BacktestResult(BaseModel):
    id: int | None = None
    status: str = "completed"
    request: BacktestRequest
    metrics: BacktestMetric
    curve: list[EquityPoint]
    periods: list[BacktestPeriod]
    notes: list[str] = Field(default_factory=list)


def model_json(value: BaseModel | dict[str, Any]) -> str:
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    import json

    return json.dumps(value, ensure_ascii=False)

