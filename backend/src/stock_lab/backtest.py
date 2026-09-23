from __future__ import annotations

import math

from .models import (
    BacktestMetric,
    BacktestPeriod,
    BacktestRequest,
    BacktestResult,
    EquityPoint,
)
from .repository import Repository
from .screening import screen_snapshots


def _annualized(total_return: float, days: int) -> float:
    if days <= 0 or total_return <= -1:
        return 0
    return (math.pow(1 + total_return, 365 / days) - 1) * 100


def _max_drawdown(values: list[float]) -> float:
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return worst * 100


def run_backtest(repository: Repository, request: BacktestRequest) -> BacktestResult:
    all_dates = repository.trading_dates(request.start_date, request.end_date)
    if len(all_dates) < 80:
        raise ValueError("回测区间至少需要80个交易日")

    # 前60个交易日仅用于计算均线和涨跌幅，从第61个交易日开始产生信号。
    dates = all_dates[60:]
    equity = request.initial_cash
    benchmark = request.initial_cash
    curve = [EquityPoint(date=dates[0], equity=equity, benchmark=benchmark)]
    periods: list[BacktestPeriod] = []
    round_trip_cost = 2 * (request.commission_rate + request.slippage_rate)

    for index in range(0, len(dates) - 1, request.rebalance_days):
        sell_index = min(index + request.rebalance_days, len(dates) - 1)
        if sell_index == index:
            break
        buy_date = dates[index]
        sell_date = dates[sell_index]
        candidates = screen_snapshots(repository.snapshots(buy_date), request.rule)
        selected = candidates[: request.position_count]
        if not selected:
            period_return = 0.0
            selected_codes: list[str] = []
        else:
            selected_codes = [stock.code for stock in selected]
            buy_prices = {stock.code: stock.close for stock in selected}
            sell_prices = repository.close_prices(selected_codes, sell_date)
            stock_returns = [
                sell_prices[code] / buy_prices[code] - 1
                for code in selected_codes
                if code in sell_prices and buy_prices[code] > 0
            ]
            period_return = (sum(stock_returns) / len(stock_returns) - round_trip_cost) if stock_returns else 0

        universe = repository.snapshots(buy_date)
        universe_codes = [stock.code for stock in universe if not stock.is_st]
        universe_buy = {stock.code: stock.close for stock in universe if not stock.is_st}
        universe_sell = repository.close_prices(universe_codes, sell_date)
        benchmark_returns = [
            universe_sell[code] / universe_buy[code] - 1
            for code in universe_codes
            if code in universe_sell and universe_buy[code] > 0
        ]
        benchmark_return = sum(benchmark_returns) / len(benchmark_returns) if benchmark_returns else 0

        equity *= 1 + period_return
        benchmark *= 1 + benchmark_return
        curve.append(EquityPoint(date=sell_date, equity=round(equity, 2), benchmark=round(benchmark, 2)))
        periods.append(
            BacktestPeriod(
                buy_date=buy_date,
                sell_date=sell_date,
                stocks=selected_codes,
                return_pct=round(period_return * 100, 2),
                ending_equity=round(equity, 2),
            )
        )

    elapsed_days = max(1, (date_from_text(curve[-1].date) - date_from_text(curve[0].date)).days)
    total_return = equity / request.initial_cash - 1
    benchmark_total = benchmark / request.initial_cash - 1
    wins = sum(period.return_pct > 0 for period in periods)
    metrics = BacktestMetric(
        total_return_pct=round(total_return * 100, 2),
        annual_return_pct=round(_annualized(total_return, elapsed_days), 2),
        max_drawdown_pct=round(_max_drawdown([point.equity for point in curve]), 2),
        benchmark_return_pct=round(benchmark_total * 100, 2),
        win_rate_pct=round(wins / len(periods) * 100, 2) if periods else 0,
        periods=len(periods),
    )
    result = BacktestResult(
        request=request,
        metrics=metrics,
        curve=curve,
        periods=periods,
        notes=[
            "当前使用确定性测试行情，不代表真实市场表现。",
            "信号仅使用买入日及此前数据，未使用未来数据。",
            "回测已计入双边佣金与滑点，暂未模拟涨跌停和停牌无法成交。",
        ],
    )
    return repository.save_backtest(result)


def date_from_text(value: str):
    from datetime import date

    return date.fromisoformat(value)

