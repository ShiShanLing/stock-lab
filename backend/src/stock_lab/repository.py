from __future__ import annotations

import json
import math
import os
import random
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .models import BacktestResult, ScreenRule, StockSnapshot, Strategy


_CST = timezone(timedelta(hours=8))


class Repository:
    def __init__(self, database_path: Path | str | None = None) -> None:
        if database_path is None:
            data_dir = Path(os.environ.get("STOCK_LAB_DATA_DIR", "data"))
            database_path = data_dir / "stock_lab.db"
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS stocks (
                    code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    market TEXT NOT NULL,
                    industry TEXT NOT NULL,
                    pe REAL NOT NULL,
                    pb REAL NOT NULL,
                    market_cap_yi REAL NOT NULL,
                    listed_days INTEGER NOT NULL,
                    is_st INTEGER NOT NULL DEFAULT 0,
                    suspended INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS daily_prices (
                    code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    close REAL NOT NULL,
                    amount_yi REAL NOT NULL,
                    turnover REAL NOT NULL,
                    volume REAL NOT NULL,
                    PRIMARY KEY (code, trade_date),
                    FOREIGN KEY (code) REFERENCES stocks(code)
                );
                CREATE INDEX IF NOT EXISTS idx_daily_prices_date
                    ON daily_prices(trade_date);
                CREATE TABLE IF NOT EXISTS strategies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    rule_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS backtests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            count = connection.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
            if count == 0:
                self._seed_demo_data(connection)

    def _seed_demo_data(self, connection: sqlite3.Connection) -> None:
        stocks = [
            ("TST001", "模拟智造", "sh", "高端制造", 18.6, 2.1, 860, 2800, 0, 0, 38.0, 0.0007),
            ("TST002", "模拟云科", "sz", "软件服务", 29.4, 4.2, 420, 1900, 0, 0, 24.0, 0.0012),
            ("TST003", "模拟医药", "sh", "医药生物", 22.8, 3.0, 650, 3500, 0, 0, 31.0, 0.0003),
            ("TST004", "模拟消费", "sz", "食品饮料", 25.1, 4.8, 980, 4100, 0, 0, 46.0, -0.0001),
            ("TST005", "模拟能源", "sh", "新能源", 16.3, 1.8, 510, 2200, 0, 0, 19.0, 0.0010),
            ("TST006", "模拟机器人", "sz", "机器人", 37.2, 6.1, 330, 760, 0, 0, 27.0, 0.0016),
            ("TST007", "模拟银行", "sh", "银行", 6.8, 0.7, 2100, 5200, 0, 0, 8.0, 0.0002),
            ("TST008", "模拟材料", "sz", "新材料", 20.4, 2.7, 280, 1400, 0, 0, 16.0, 0.0008),
            ("TST009", "模拟通信", "sh", "通信设备", 31.5, 5.0, 720, 1800, 0, 0, 34.0, 0.0011),
            ("TST010", "模拟物流", "sz", "交通运输", 14.9, 1.5, 190, 2600, 0, 0, 12.0, 0.0004),
            ("TST011", "ST模拟风险", "sh", "风险样本", 58.0, 8.5, 45, 1600, 1, 0, 5.0, -0.0010),
            ("TST012", "模拟新股", "sz", "次新样本", 42.0, 7.2, 95, 120, 0, 0, 52.0, 0.0018),
        ]
        connection.executemany(
            """
            INSERT INTO stocks (
                code, name, market, industry, pe, pb, market_cap_yi,
                listed_days, is_st, suspended
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [row[:10] for row in stocks],
        )

        end = date.today()
        trading_dates: list[date] = []
        cursor = end - timedelta(days=520)
        while cursor <= end:
            if cursor.weekday() < 5:
                trading_dates.append(cursor)
            cursor += timedelta(days=1)

        price_rows: list[tuple[object, ...]] = []
        for stock_index, stock in enumerate(stocks):
            code, *_unused, start_price, drift = stock
            rng = random.Random(20260923 + stock_index)
            price = float(start_price)
            volume_base = 1_000_000 + stock_index * 170_000
            for day_index, trading_date in enumerate(trading_dates):
                cycle = math.sin(day_index / (13 + stock_index % 5)) * 0.004
                shock = rng.gauss(0, 0.011 + (stock_index % 4) * 0.002)
                price = max(2.0, price * (1 + float(drift) + cycle + shock))
                volume_wave = 1 + math.sin(day_index / 9 + stock_index) * 0.25
                volume = max(100_000, volume_base * volume_wave * (0.8 + rng.random() * 0.4))
                amount_yi = price * volume / 1e8
                turnover = 0.8 + stock_index * 0.12 + rng.random() * 2.2
                price_rows.append(
                    (
                        code,
                        trading_date.isoformat(),
                        round(price, 2),
                        round(amount_yi, 3),
                        round(turnover, 2),
                        round(volume, 2),
                    )
                )
        connection.executemany(
            """
            INSERT INTO daily_prices
                (code, trade_date, close, amount_yi, turnover, volume)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            price_rows,
        )

    def trading_dates(self, start: str | None = None, end: str | None = None) -> list[str]:
        clauses: list[str] = []
        params: list[str] = []
        if start:
            clauses.append("trade_date >= ?")
            params.append(start)
        if end:
            clauses.append("trade_date <= ?")
            params.append(end)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connection() as connection:
            rows = connection.execute(
                f"SELECT DISTINCT trade_date FROM daily_prices {where} ORDER BY trade_date",
                params,
            ).fetchall()
        return [row[0] for row in rows]

    def latest_trade_date(self) -> str:
        return self.trading_dates()[-1]

    def snapshots(self, trade_date: str | None = None) -> list[StockSnapshot]:
        target = trade_date or self.latest_trade_date()
        with self.connection() as connection:
            stocks = connection.execute("SELECT * FROM stocks ORDER BY code").fetchall()
            snapshots: list[StockSnapshot] = []
            for stock in stocks:
                prices = connection.execute(
                    """
                    SELECT * FROM daily_prices
                    WHERE code = ? AND trade_date <= ?
                    ORDER BY trade_date DESC LIMIT 61
                    """,
                    (stock["code"], target),
                ).fetchall()
                if not prices:
                    continue
                latest = prices[0]
                closes = [float(row["close"]) for row in reversed(prices)]
                volumes = [float(row["volume"]) for row in reversed(prices)]
                returns = [
                    closes[index] / closes[index - 1] - 1
                    for index in range(max(1, len(closes) - 20), len(closes))
                    if closes[index - 1] > 0
                ]
                mean_return = sum(returns) / len(returns) if returns else 0
                variance = (
                    sum((value - mean_return) ** 2 for value in returns) / len(returns)
                    if returns
                    else 0
                )
                ma5 = sum(closes[-5:]) / min(5, len(closes))
                ma20 = sum(closes[-20:]) / min(20, len(closes))
                ma60 = sum(closes[-60:]) / min(60, len(closes))
                base_20 = closes[-21] if len(closes) >= 21 else closes[0]
                recent_volume = sum(volumes[-5:]) / min(5, len(volumes))
                previous_volume_slice = volumes[-25:-5] or volumes[:-5] or volumes
                previous_volume = sum(previous_volume_slice) / len(previous_volume_slice)
                snapshots.append(
                    StockSnapshot(
                        code=stock["code"],
                        name=stock["name"],
                        market=stock["market"],
                        industry=stock["industry"],
                        trade_date=latest["trade_date"],
                        close=float(latest["close"]),
                        pe=float(stock["pe"]),
                        pb=float(stock["pb"]),
                        market_cap_yi=float(stock["market_cap_yi"]),
                        amount_yi=float(latest["amount_yi"]),
                        turnover=float(latest["turnover"]),
                        change_20d=round((closes[-1] / base_20 - 1) * 100, 2),
                        volume_ratio_5d=round(recent_volume / previous_volume, 2) if previous_volume else 0,
                        volatility_20d=round(math.sqrt(variance) * math.sqrt(252) * 100, 2),
                        ma5=round(ma5, 2),
                        ma20=round(ma20, 2),
                        ma60=round(ma60, 2),
                        ma_bullish=ma5 > ma20 > ma60,
                        listed_days=int(stock["listed_days"]),
                        is_st=bool(stock["is_st"]),
                        suspended=bool(stock["suspended"]),
                    )
                )
        return snapshots

    def close_prices(self, codes: list[str], trade_date: str) -> dict[str, float]:
        if not codes:
            return {}
        placeholders = ",".join("?" for _ in codes)
        with self.connection() as connection:
            rows = connection.execute(
                f"SELECT code, close FROM daily_prices WHERE trade_date = ? AND code IN ({placeholders})",
                [trade_date, *codes],
            ).fetchall()
        return {row["code"]: float(row["close"]) for row in rows}

    def save_strategy(self, name: str, description: str, rule: ScreenRule) -> Strategy:
        created_at = datetime.now(_CST).isoformat()
        with self.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO strategies (name, description, rule_json, created_at) VALUES (?, ?, ?, ?)",
                (name, description, rule.model_dump_json(), created_at),
            )
            strategy_id = int(cursor.lastrowid)
        return Strategy(
            id=strategy_id,
            name=name,
            description=description,
            rule=rule,
            created_at=created_at,
        )

    def list_strategies(self) -> list[Strategy]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM strategies ORDER BY id DESC").fetchall()
        return [
            Strategy(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                rule=ScreenRule.model_validate_json(row["rule_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def save_backtest(self, result: BacktestResult) -> BacktestResult:
        created_at = datetime.now(_CST).isoformat()
        with self.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO backtests (result_json, created_at) VALUES (?, ?)",
                (result.model_dump_json(), created_at),
            )
            result.id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE backtests SET result_json = ? WHERE id = ?",
                (result.model_dump_json(), result.id),
            )
        return result


_repository: Repository | None = None


def get_repository() -> Repository:
    global _repository
    if _repository is None:
        _repository = Repository()
    return _repository


def reset_repository() -> None:
    global _repository
    _repository = None
