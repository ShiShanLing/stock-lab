from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable

import duckdb
import pyarrow as pa


@dataclass(frozen=True)
class StrategySpec:
    key: str
    name: str
    family: str
    holding_days: int
    position_count: int
    scorer: Callable[[dict[str, Any]], float | None]
    regime: str = "always"
    stop_loss_pct: float | None = None


@dataclass(frozen=True)
class ResearchPeriod:
    signal_date: str
    entry_date: str
    exit_date: str


def _finite(value: Any) -> bool:
    return value is not None and math.isfinite(float(value))


def _common(row: dict[str, Any]) -> bool:
    name = str(row["name"]).upper()
    return (
        "ST" not in name
        and "退" not in name
        and _finite(row["entry_open"])
        and _finite(row["exit_open"])
        and float(row["entry_open"]) > 0
        and float(row["exit_open"]) > 0
        and _finite(row["avg_amount_20"])
        and float(row["avg_amount_20"]) >= 20_000_000
        and _finite(row["ret_120"])
        and _finite(row["ma_120"])
        and _finite(row["vol_60"])
        and _finite(row["holding_max_abs_return"])
        and float(row["holding_max_abs_return"]) <= 0.50
    )


def _momentum_120(row: dict[str, Any]) -> float | None:
    if not _common(row) or row["ret_120"] <= 0 or row["close"] <= row["ma_120"]:
        return None
    if not _finite(row["ret_20"]) or row["ret_20"] < -0.15:
        return None
    return float(row["ret_120"])


def _dual_momentum(row: dict[str, Any]) -> float | None:
    if not _common(row) or not _finite(row["ret_60"]):
        return None
    if row["ret_60"] <= 0 or row["ret_120"] <= 0 or row["close"] <= row["ma_120"]:
        return None
    return 0.6 * float(row["ret_60"]) + 0.4 * float(row["ret_120"])


def _risk_adjusted_trend(row: dict[str, Any]) -> float | None:
    if not _common(row) or not all(
        _finite(row[key]) for key in ("ma_20", "ma_60", "ret_60")
    ):
        return None
    if not (row["close"] > row["ma_20"] > row["ma_60"] > row["ma_120"]):
        return None
    volatility = max(float(row["vol_60"]), 0.01)
    return (0.7 * float(row["ret_60"]) + 0.3 * float(row["ret_120"])) / volatility


def _low_volatility_trend(row: dict[str, Any]) -> float | None:
    if not _common(row) or row["ret_120"] <= 0 or row["close"] <= row["ma_120"]:
        return None
    return -float(row["vol_60"]) + 0.15 * float(row["ret_120"])


def _trend_pullback(row: dict[str, Any]) -> float | None:
    if not _common(row) or not _finite(row["ret_5"]):
        return None
    if row["ret_120"] < 0.10 or row["close"] <= row["ma_120"]:
        return None
    if not (-0.12 <= row["ret_5"] <= 0.01):
        return None
    return float(row["ret_120"]) - abs(float(row["ret_5"]))


def _near_breakout(row: dict[str, Any]) -> float | None:
    if not _common(row) or not _finite(row["high_120"]):
        return None
    distance = float(row["close"]) / float(row["high_120"])
    if row["ret_120"] <= 0 or distance < 0.95 or row["close"] <= row["ma_120"]:
        return None
    return float(row["ret_120"]) + distance * 0.1


def _momentum_12_1(row: dict[str, Any]) -> float | None:
    if not _common(row) or not _finite(row["ret_240"]) or not _finite(row["ret_20"]):
        return None
    if row["ret_240"] <= 0 or row["close"] <= row["ma_120"]:
        return None
    return (1 + float(row["ret_240"])) / max(1 + float(row["ret_20"]), 0.01) - 1


def _conservative_trend(row: dict[str, Any]) -> float | None:
    if not _common(row) or not all(
        _finite(row[key]) for key in ("ret_20", "ret_60", "ma_20", "ma_60")
    ):
        return None
    if not (row["close"] > row["ma_20"] > row["ma_60"] > row["ma_120"]):
        return None
    if not (0 < row["ret_20"] < 0.20 and 0 < row["ret_60"] < 0.40):
        return None
    if not (0 < row["ret_120"] < 0.60 and row["vol_60"] < 0.50):
        return None
    return -float(row["vol_60"]) + 0.20 * float(row["ret_60"])


def candidate_strategies() -> list[StrategySpec]:
    families = [
        ("momentum120", "中期动量", _momentum_120, "always"),
        ("dual_momentum", "双周期动量", _dual_momentum, "always"),
        ("risk_trend", "风险调整趋势", _risk_adjusted_trend, "always"),
        ("low_vol_trend", "低波动趋势", _low_volatility_trend, "always"),
        ("trend_pullback", "趋势回撤", _trend_pullback, "always"),
        ("near_breakout", "临近120日新高", _near_breakout, "always"),
        ("breadth45_momentum", "市场宽度45%·中期动量", _momentum_120, "breadth45"),
        ("breadth55_dual", "市场宽度55%·双周期动量", _dual_momentum, "breadth55"),
        ("breadth45_low_vol", "市场宽度45%·低波动趋势", _low_volatility_trend, "breadth45"),
        ("breadth55_low_vol", "市场宽度55%·低波动趋势", _low_volatility_trend, "breadth55"),
        ("breadth45_12_1", "市场宽度45%·12减1月动量", _momentum_12_1, "breadth45"),
        ("breadth55_12_1", "市场宽度55%·12减1月动量", _momentum_12_1, "breadth55"),
        ("breadth45_conservative", "市场宽度45%·保守趋势", _conservative_trend, "breadth45"),
        ("breadth50_conservative", "市场宽度50%·保守趋势", _conservative_trend, "breadth50"),
        ("breadth55_conservative", "市场宽度55%·保守趋势", _conservative_trend, "breadth55"),
    ]
    result: list[StrategySpec] = []
    for family, name, scorer, regime in families:
        for holding_days in (10, 20):
            for position_count in (5, 10, 20):
                result.append(
                    StrategySpec(
                        key=f"{family}_h{holding_days}_n{position_count}",
                        name=f"{name}·持有{holding_days}日·{position_count}只",
                        family=family,
                        holding_days=holding_days,
                        position_count=position_count,
                        scorer=scorer,
                        regime=regime,
                    )
                )
    for stop_loss in (0.08, 0.10):
        for position_count in (5, 10):
            percent = int(stop_loss * 100)
            result.append(
                StrategySpec(
                    key=f"breadth45_conservative_stop{percent}_h10_n{position_count}",
                    name=f"市场宽度45%·保守趋势·止损{percent}%·{position_count}只",
                    family=f"breadth45_conservative_stop{percent}",
                    holding_days=10,
                    position_count=position_count,
                    scorer=_conservative_trend,
                    regime="breadth45",
                    stop_loss_pct=stop_loss,
                )
            )
    return result


def _calendar_periods(
    connection: duckdb.DuckDBPyConnection,
    start_date: str,
    end_date: str,
    holding_days: int,
) -> list[ResearchPeriod]:
    dates = [
        str(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT trade_date FROM daily_bars
            WHERE trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
            ORDER BY trade_date
            """,
            [start_date, end_date],
        ).fetchall()
    ]
    return [
        ResearchPeriod(dates[index], dates[index + 1], dates[index + holding_days + 1])
        for index in range(0, len(dates) - holding_days - 1, holding_days)
    ]


def _load_rows(
    connection: duckdb.DuckDBPyConnection,
    periods: list[ResearchPeriod],
    holding_days: int,
) -> dict[str, list[dict[str, Any]]]:
    if not periods:
        return {}
    table = pa.Table.from_pylist([asdict(period) for period in periods])
    connection.register("research_periods", table)
    try:
        if holding_days not in {10, 20}:
            raise ValueError("研究持有期仅允许10或20个交易日")
        cursor = connection.execute(
            f"""
            WITH base AS (
                SELECT
                    b.secid, b.trade_date, b.close, b.low, b.amount,
                    b.close / NULLIF(LAG(b.close, 5) OVER stock_window, 0) - 1 AS ret_5,
                    b.close / NULLIF(LAG(b.close, 20) OVER stock_window, 0) - 1 AS ret_20,
                    b.close / NULLIF(LAG(b.close, 60) OVER stock_window, 0) - 1 AS ret_60,
                    b.close / NULLIF(LAG(b.close, 120) OVER stock_window, 0) - 1 AS ret_120,
                    b.close / NULLIF(LAG(b.close, 240) OVER stock_window, 0) - 1 AS ret_240,
                    b.close / NULLIF(LAG(b.close, 1) OVER stock_window, 0) - 1 AS daily_return,
                    AVG(b.close) OVER (
                        PARTITION BY b.secid ORDER BY b.trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                    ) AS ma_20,
                    AVG(b.close) OVER (
                        PARTITION BY b.secid ORDER BY b.trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                    ) AS ma_60,
                    AVG(b.close) OVER (
                        PARTITION BY b.secid ORDER BY b.trade_date ROWS BETWEEN 119 PRECEDING AND CURRENT ROW
                    ) AS ma_120,
                    MAX(b.close) OVER (
                        PARTITION BY b.secid ORDER BY b.trade_date ROWS BETWEEN 119 PRECEDING AND CURRENT ROW
                    ) AS high_120,
                    AVG(b.amount) OVER (
                        PARTITION BY b.secid ORDER BY b.trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                    ) AS avg_amount_20
                FROM daily_bars b
                WINDOW stock_window AS (PARTITION BY b.secid ORDER BY b.trade_date)
            ), features AS (
                SELECT base.*,
                    STDDEV_SAMP(daily_return) OVER (
                        PARTITION BY secid ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                    ) * SQRT(252) AS vol_60,
                    MAX(ABS(daily_return)) OVER (
                        PARTITION BY secid ORDER BY trade_date
                        ROWS BETWEEN 1 FOLLOWING AND {holding_days + 1} FOLLOWING
                    ) AS holding_max_abs_return,
                    MIN(low) OVER (
                        PARTITION BY secid ORDER BY trade_date
                        ROWS BETWEEN 1 FOLLOWING AND {holding_days + 1} FOLLOWING
                    ) AS holding_min_low
                FROM base
            )
            SELECT
                CAST(p.signal_date AS VARCHAR) AS signal_date,
                s.code, s.name, f.secid, f.close,
                f.ret_5, f.ret_20, f.ret_60, f.ret_120, f.ret_240,
                f.ma_20, f.ma_60, f.ma_120, f.high_120, f.vol_60, f.avg_amount_20,
                f.holding_max_abs_return,
                f.holding_min_low,
                entry_bar.open AS entry_open, exit_bar.open AS exit_open
            FROM research_periods p
            JOIN features f ON f.trade_date = CAST(p.signal_date AS DATE)
            JOIN stocks s ON s.secid = f.secid
            JOIN daily_bars entry_bar
              ON entry_bar.secid = f.secid AND entry_bar.trade_date = CAST(p.entry_date AS DATE)
            JOIN daily_bars exit_bar
              ON exit_bar.secid = f.secid AND exit_bar.trade_date = CAST(p.exit_date AS DATE)
            ORDER BY p.signal_date, f.secid
            """
        )
        columns = [item[0] for item in cursor.description]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for values in cursor.fetchall():
            row = dict(zip(columns, values, strict=True))
            grouped.setdefault(row["signal_date"], []).append(row)
        return grouped
    finally:
        connection.unregister("research_periods")


def _max_drawdown(curve: list[float]) -> float:
    peak = curve[0]
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst * 100


def evaluate_strategy(
    spec: StrategySpec,
    periods: list[ResearchPeriod],
    rows_by_date: dict[str, list[dict[str, Any]]],
    round_trip_cost: float = 0.0016,
) -> dict[str, Any]:
    equity = 1.0
    benchmark = 1.0
    curve = [equity]
    period_returns: list[float] = []
    trade_returns: list[float] = []
    invested_periods = 0
    holdings_total = 0
    details: list[dict[str, Any]] = []
    for period in periods:
        rows = rows_by_date.get(period.signal_date, [])
        scored: list[tuple[float, dict[str, Any]]] = []
        benchmark_returns: list[float] = []
        eligible_signal_rows = [row for row in rows if _common(row)]
        breadth = (
            mean(float(row["close"] > row["ma_120"]) for row in eligible_signal_rows)
            if eligible_signal_rows
            else 0.0
        )
        regime_open = (
            spec.regime == "always"
            or (spec.regime == "breadth45" and breadth >= 0.45)
            or (spec.regime == "breadth50" and breadth >= 0.50)
            or (spec.regime == "breadth55" and breadth >= 0.55)
        )
        for row in rows:
            if _common(row):
                benchmark_returns.append(float(row["exit_open"]) / float(row["entry_open"]) - 1)
            score = spec.scorer(row) if regime_open else None
            if score is not None and math.isfinite(score):
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        selected = scored[: spec.position_count]
        returns: list[float] = []
        for _, row in selected:
            raw_return = float(row["exit_open"]) / float(row["entry_open"]) - 1
            if (
                spec.stop_loss_pct is not None
                and _finite(row["holding_min_low"])
                and float(row["holding_min_low"])
                <= float(row["entry_open"]) * (1 - spec.stop_loss_pct)
            ):
                raw_return = -spec.stop_loss_pct
            returns.append(raw_return - round_trip_cost)
        period_return = mean(returns) if returns else 0.0
        benchmark_return = mean(benchmark_returns) if benchmark_returns else 0.0
        if returns:
            invested_periods += 1
            holdings_total += len(returns)
            trade_returns.extend(returns)
        period_returns.append(period_return)
        equity *= 1 + period_return
        benchmark *= 1 + benchmark_return
        curve.append(equity)
        details.append(
            {
                "signal_date": period.signal_date,
                "entry_date": period.entry_date,
                "exit_date": period.exit_date,
                "return_pct": round(period_return * 100, 4),
                "holdings": [row["code"] for _, row in selected],
                "market_breadth_pct": round(breadth * 100, 2),
            }
        )
    elapsed_days = max(
        1,
        (date.fromisoformat(periods[-1].exit_date) - date.fromisoformat(periods[0].entry_date)).days,
    )
    annual_return = (math.pow(max(equity, 0.000001), 365 / elapsed_days) - 1) * 100
    benchmark_annual = (math.pow(max(benchmark, 0.000001), 365 / elapsed_days) - 1) * 100
    active_returns = [
        detail["return_pct"] / 100 for detail in details if detail["holdings"]
    ]
    wins = sum(value > 0 for value in active_returns)
    calendar_wins = sum(value > 0 for value in period_returns)
    trade_wins = sum(value > 0 for value in trade_returns)
    return {
        "strategy_key": spec.key,
        "strategy_name": spec.name,
        "family": spec.family,
        "holding_days": spec.holding_days,
        "position_count": spec.position_count,
        "stop_loss_pct": spec.stop_loss_pct,
        "periods": len(period_returns),
        "invested_periods": invested_periods,
        "participation_rate_pct": round(
            invested_periods / max(len(period_returns), 1) * 100, 2
        ),
        "average_holdings": round(holdings_total / max(invested_periods, 1), 2),
        "total_return_pct": round((equity - 1) * 100, 2),
        "annual_return_pct": round(annual_return, 2),
        "max_drawdown_pct": round(_max_drawdown(curve), 2),
        "period_win_rate_pct": round(wins / max(invested_periods, 1) * 100, 2),
        "calendar_win_rate_pct": round(
            calendar_wins / max(len(period_returns), 1) * 100, 2
        ),
        "trade_win_rate_pct": round(trade_wins / max(len(trade_returns), 1) * 100, 2),
        "benchmark_total_return_pct": round((benchmark - 1) * 100, 2),
        "benchmark_annual_return_pct": round(benchmark_annual, 2),
        "annual_excess_pct": round(annual_return - benchmark_annual, 2),
        "details": details,
    }


def _score(metrics: dict[str, Any]) -> float:
    target_bonus = (
        50.0
        if metrics["period_win_rate_pct"] >= 60
        and metrics["invested_periods"] >= 12
        and metrics["participation_rate_pct"] >= 35
        else 0.0
    )
    return (
        target_bonus
        + metrics["annual_excess_pct"]
        + 0.25 * metrics["annual_return_pct"]
        + 0.20 * metrics["period_win_rate_pct"]
        + 0.35 * metrics["max_drawdown_pct"]
    )


def run_research(
    database_path: Path | str,
    output_dir: Path | str,
    development: tuple[str, str] = ("2019-01-01", "2021-12-31"),
    validation: tuple[str, str] = ("2022-01-01", "2023-12-31"),
    blind_test: tuple[str, str] = ("2024-01-01", "2026-09-23"),
) -> Path:
    database = Path(database_path).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    specs = candidate_strategies()
    results: dict[str, dict[str, dict[str, Any]]] = {
        "development": {},
        "validation": {},
        "blind_test": {},
    }
    with duckdb.connect(str(database), read_only=True) as connection:
        for holding_days in sorted({spec.holding_days for spec in specs}):
            matching = [spec for spec in specs if spec.holding_days == holding_days]
            for split_name, date_range in (
                ("development", development),
                ("validation", validation),
            ):
                periods = _calendar_periods(connection, *date_range, holding_days)
                rows = _load_rows(connection, periods, holding_days)
                for spec in matching:
                    results[split_name][spec.key] = evaluate_strategy(spec, periods, rows)

        ranked = sorted(
            specs,
            key=lambda item: _score(results["validation"][item.key]),
            reverse=True,
        )
        # 最终测试集只评估在验证集排名靠前、且每个策略家族最多一个的候选。
        finalists: list[StrategySpec] = []
        used_families: set[str] = set()
        for spec in ranked:
            if spec.family in used_families:
                continue
            finalists.append(spec)
            used_families.add(spec.family)
            if len(finalists) == 3:
                break
        for holding_days in sorted({spec.holding_days for spec in finalists}):
            periods = _calendar_periods(connection, *blind_test, holding_days)
            rows = _load_rows(connection, periods, holding_days)
            for spec in finalists:
                if spec.holding_days == holding_days:
                    results["blind_test"][spec.key] = evaluate_strategy(spec, periods, rows)

    reliable: list[str] = []
    for spec in finalists:
        validation_metrics = results["validation"][spec.key]
        test_metrics = results["blind_test"][spec.key]
        if (
            validation_metrics["period_win_rate_pct"] >= 60
            and test_metrics["period_win_rate_pct"] >= 60
            and test_metrics["periods"] >= 24
            and validation_metrics["invested_periods"] >= 12
            and test_metrics["invested_periods"] >= 24
            and test_metrics["participation_rate_pct"] >= 35
            and test_metrics["annual_excess_pct"] > 0
            and test_metrics["max_drawdown_pct"] > -30
        ):
            reliable.append(spec.key)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": str(database),
        "methodology": {
            "development": development,
            "validation": validation,
            "blind_test": blind_test,
            "round_trip_cost": 0.0016,
            "candidate_count": len(specs),
            "finalist_count": len(finalists),
            "target": "验证集和确认集的实际开仓周期胜率均不低于60%，确认集至少24个开仓周期且参与率不低于35%、年化超额为正、最大回撤优于-30%",
            "warning": "历史回测不能保证未来盈利；第二代策略是在第一代结果可见后设计，确认集不再属于完全未见盲测；当前股票目录仍可能存在幸存者偏差。",
        },
        "finalists": [spec.key for spec in finalists],
        "reliable_candidates": reliable,
        "results": results,
    }
    path = output / "strategy_research.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
