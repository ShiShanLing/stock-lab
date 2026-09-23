from __future__ import annotations

import operator
from collections.abc import Callable

from .models import Condition, ScreenResult, ScreenRule, StockSnapshot
from .repository import Repository


_OPERATORS: dict[str, Callable[[object, object], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
}

FIELD_LABELS = {
    "pe": "市盈率",
    "pb": "市净率",
    "market_cap_yi": "总市值",
    "amount_yi": "成交额",
    "turnover": "换手率",
    "change_20d": "近20日涨跌幅",
    "volume_ratio_5d": "近5日量比",
    "volatility_20d": "近20日年化波动",
    "ma_bullish": "均线多头",
}


def _matches_condition(stock: StockSnapshot, condition: Condition) -> bool:
    if not hasattr(stock, condition.field):
        return False
    current = getattr(stock, condition.field)
    return _OPERATORS[condition.operator](current, condition.value)


def _reason(condition: Condition) -> str:
    label = condition.label or FIELD_LABELS.get(condition.field, condition.field)
    if isinstance(condition.value, bool):
        return label if condition.value else f"非{label}"
    return f"{label} {condition.operator} {condition.value:g}"


def screen_snapshots(stocks: list[StockSnapshot], rule: ScreenRule) -> list[StockSnapshot]:
    matched: list[StockSnapshot] = []
    for stock in stocks:
        if stock.market not in rule.markets:
            continue
        if rule.exclude_st and stock.is_st:
            continue
        if rule.exclude_suspended and stock.suspended:
            continue
        if stock.listed_days < rule.min_listed_days:
            continue
        if not all(_matches_condition(stock, condition) for condition in rule.conditions):
            continue
        copy = stock.model_copy(deep=True)
        copy.reasons = [_reason(condition) for condition in rule.conditions]
        matched.append(copy)

    reverse = rule.sort.direction == "desc"
    matched.sort(
        key=lambda stock: getattr(stock, rule.sort.field, float("-inf")),
        reverse=reverse,
    )
    return matched[: rule.limit]


def run_screen(repository: Repository, rule: ScreenRule, trade_date: str | None = None) -> ScreenResult:
    stocks = repository.snapshots(trade_date)
    selected = screen_snapshots(stocks, rule)
    actual_date = selected[0].trade_date if selected else (trade_date or repository.latest_trade_date())
    return ScreenResult(
        trade_date=actual_date,
        total_universe=len(stocks),
        matched_count=len(selected),
        stocks=selected,
    )

