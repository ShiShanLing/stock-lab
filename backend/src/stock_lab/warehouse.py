from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

from .data_sources import DailyBar, StockIdentity


_CST = timezone(timedelta(hours=8))


def _iso_date(value: str) -> str:
    text = value.replace("-", "")
    return datetime.strptime(text, "%Y%m%d").date().isoformat()


class MarketWarehouse:
    def __init__(self, data_dir: Path | str | None = None) -> None:
        root = Path(data_dir or os.environ.get("STOCK_LAB_MARKET_DIR", "data/market"))
        self.data_dir = root.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = self.data_dir / "market.duckdb"
        self.initialize()

    def connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.database_path))

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS warehouse_metadata (
                    key VARCHAR PRIMARY KEY,
                    value VARCHAR NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stocks (
                    secid VARCHAR PRIMARY KEY,
                    code VARCHAR NOT NULL,
                    name VARCHAR NOT NULL,
                    market INTEGER NOT NULL,
                    list_date DATE,
                    source VARCHAR NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_bars (
                    secid VARCHAR NOT NULL,
                    trade_date DATE NOT NULL,
                    open DOUBLE NOT NULL,
                    close DOUBLE NOT NULL,
                    high DOUBLE NOT NULL,
                    low DOUBLE NOT NULL,
                    volume DOUBLE NOT NULL,
                    amount DOUBLE NOT NULL,
                    amplitude_pct DOUBLE NOT NULL,
                    change_pct DOUBLE NOT NULL,
                    change_amount DOUBLE NOT NULL,
                    turnover_pct DOUBLE NOT NULL,
                    source VARCHAR NOT NULL,
                    loaded_at TIMESTAMP NOT NULL,
                    PRIMARY KEY (secid, trade_date)
                );
                CREATE TABLE IF NOT EXISTS sync_state (
                    secid VARCHAR NOT NULL,
                    dataset VARCHAR NOT NULL,
                    start_date DATE,
                    end_date DATE,
                    status VARCHAR NOT NULL,
                    row_count BIGINT NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error VARCHAR,
                    updated_at TIMESTAMP NOT NULL,
                    PRIMARY KEY (secid, dataset)
                );
                """
            )

    def set_metadata(self, key: str, value: str) -> None:
        now = datetime.now(_CST).replace(tzinfo=None)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO warehouse_metadata VALUES (?, ?, ?)
                ON CONFLICT (key) DO UPDATE
                SET value = excluded.value, updated_at = excluded.updated_at
                """,
                [key, value, now],
            )

    def upsert_stocks(self, stocks: Iterable[StockIdentity]) -> int:
        now = datetime.now(_CST).replace(tzinfo=None)
        rows = [
            [stock.secid, stock.code, stock.name, stock.market, stock.list_date, stock.source, now]
            for stock in stocks
        ]
        if not rows:
            return 0
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO stocks VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (secid) DO UPDATE SET
                    code = excluded.code,
                    name = excluded.name,
                    market = excluded.market,
                    list_date = COALESCE(excluded.list_date, stocks.list_date),
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
        self.set_metadata("catalog_updated_at", datetime.now(_CST).isoformat())
        return len(rows)

    def stocks_for_sync(
        self,
        start_date: str,
        end_date: str,
        retry_failed: bool = True,
        limit: int | None = None,
    ) -> list[StockIdentity]:
        retry_clause = "OR x.status <> 'complete'" if retry_failed else ""
        sql = f"""
            SELECT s.secid, s.code, s.name, s.market, CAST(s.list_date AS VARCHAR), s.source
            FROM stocks s
            LEFT JOIN sync_state x ON x.secid = s.secid AND x.dataset = 'daily_bars'
            WHERE x.secid IS NULL
               {retry_clause}
               OR (x.status = 'complete' AND (
                    x.start_date > CAST(? AS DATE)
                    OR x.end_date < CAST(? AS DATE)
               ))
            ORDER BY s.secid
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self.connect() as connection:
            rows = connection.execute(
                sql, [_iso_date(start_date), _iso_date(end_date)]
            ).fetchall()
        return [
            StockIdentity(
                secid=row[0],
                code=row[1],
                name=row[2],
                market=int(row[3]),
                list_date=row[4],
                source=row[5],
            )
            for row in rows
        ]

    def mark_sync_started(self, secid: str, start_date: str, end_date: str) -> None:
        now = datetime.now(_CST).replace(tzinfo=None)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO sync_state
                    (secid, dataset, start_date, end_date, status, row_count, attempts, error, updated_at)
                VALUES (?, 'daily_bars', ?, ?, 'running', 0, 1, NULL, ?)
                ON CONFLICT (secid, dataset) DO UPDATE SET
                    start_date = excluded.start_date,
                    end_date = excluded.end_date,
                    status = 'running',
                    attempts = sync_state.attempts + 1,
                    error = NULL,
                    updated_at = excluded.updated_at
                """,
                [secid, _iso_date(start_date), _iso_date(end_date), now],
            )

    def save_daily_bars(
        self,
        secid: str,
        bars: list[DailyBar],
        start_date: str,
        end_date: str,
    ) -> int:
        now = datetime.now(_CST).replace(tzinfo=None)
        rows = [
            {
                "secid": bar.secid,
                "trade_date": bar.trade_date,
                "open": bar.open,
                "close": bar.close,
                "high": bar.high,
                "low": bar.low,
                "volume": bar.volume,
                "amount": bar.amount,
                "amplitude_pct": bar.amplitude_pct,
                "change_pct": bar.change_pct,
                "change_amount": bar.change_amount,
                "turnover_pct": bar.turnover_pct,
                "source": bar.source,
                "loaded_at": now,
            }
            for bar in bars
        ]
        with self.connect() as connection:
            if rows:
                incoming = pa.Table.from_pylist(rows)
                connection.register("incoming_daily_bars", incoming)
                try:
                    connection.execute(
                        """
                        INSERT INTO daily_bars
                        SELECT
                            secid, CAST(trade_date AS DATE), open, close, high, low,
                            volume, amount, amplitude_pct, change_pct, change_amount,
                            turnover_pct, source, loaded_at
                        FROM incoming_daily_bars
                        ON CONFLICT (secid, trade_date) DO UPDATE SET
                            open = excluded.open,
                            close = excluded.close,
                            high = excluded.high,
                            low = excluded.low,
                            volume = excluded.volume,
                            amount = excluded.amount,
                            amplitude_pct = excluded.amplitude_pct,
                            change_pct = excluded.change_pct,
                            change_amount = excluded.change_amount,
                            turnover_pct = excluded.turnover_pct,
                            source = excluded.source,
                            loaded_at = excluded.loaded_at
                        """
                    )
                finally:
                    connection.unregister("incoming_daily_bars")
            connection.execute(
                """
                UPDATE sync_state SET status = 'complete', row_count = ?, error = NULL, updated_at = ?
                WHERE secid = ? AND dataset = 'daily_bars'
                """,
                [len(rows), now, secid],
            )
        return len(rows)

    def mark_sync_failed(self, secid: str, error: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE sync_state SET status = 'failed', error = ?, updated_at = ?
                WHERE secid = ? AND dataset = 'daily_bars'
                """,
                [error[:1000], datetime.now(_CST).replace(tzinfo=None), secid],
            )

    def summary(self) -> dict[str, Any]:
        with self.connect() as connection:
            stock_count = connection.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
            bar_stats = connection.execute(
                "SELECT COUNT(*), MIN(trade_date), MAX(trade_date), COUNT(DISTINCT secid) FROM daily_bars"
            ).fetchone()
            states = dict(
                connection.execute(
                    "SELECT status, COUNT(*) FROM sync_state GROUP BY status"
                ).fetchall()
            )
            failures = connection.execute(
                """
                SELECT secid, error FROM sync_state
                WHERE status = 'failed' ORDER BY updated_at DESC LIMIT 10
                """
            ).fetchall()
        return {
            "database": str(self.database_path),
            "stock_count": int(stock_count),
            "bar_count": int(bar_stats[0]),
            "min_date": str(bar_stats[1]) if bar_stats[1] else None,
            "max_date": str(bar_stats[2]) if bar_stats[2] else None,
            "covered_stocks": int(bar_stats[3]),
            "sync_states": {str(key): int(value) for key, value in states.items()},
            "recent_failures": [{"secid": row[0], "error": row[1]} for row in failures],
        }

    def verify(self) -> dict[str, Any]:
        with self.connect() as connection:
            duplicate_count = connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT secid, trade_date, COUNT(*) amount
                    FROM daily_bars GROUP BY secid, trade_date HAVING amount > 1
                )
                """
            ).fetchone()[0]
            invalid_ohlc = connection.execute(
                """
                SELECT COUNT(*) FROM daily_bars
                WHERE close <= 0 OR open <= 0 OR high < GREATEST(open, close, low)
                   OR low > LEAST(open, close, high) OR volume < 0 OR amount < 0
                """
            ).fetchone()[0]
            short_histories = connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT secid, COUNT(*) amount FROM daily_bars GROUP BY secid HAVING amount < 60
                )
                """
            ).fetchone()[0]
        summary = self.summary()
        return {
            **summary,
            "duplicate_count": int(duplicate_count),
            "invalid_ohlc_count": int(invalid_ohlc),
            "stocks_with_less_than_60_bars": int(short_histories),
            "passed": duplicate_count == 0 and invalid_ohlc == 0,
        }

    def export_snapshot(self, version: str) -> Path:
        snapshot_dir = self.data_dir / "snapshots" / version
        if snapshot_dir.exists():
            raise FileExistsError(f"数据快照已存在且不可覆盖：{snapshot_dir}")
        snapshot_dir.mkdir(parents=True)
        stocks_path = snapshot_dir / "stocks.parquet"
        daily_path = snapshot_dir / "daily"
        with self.connect() as connection:
            connection.execute(
                f"COPY (SELECT * FROM stocks ORDER BY secid) TO '{stocks_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            connection.execute(
                f"""
                COPY (
                    SELECT *, YEAR(trade_date) AS trade_year FROM daily_bars
                    ORDER BY secid, trade_date
                ) TO '{daily_path}'
                (FORMAT PARQUET, PARTITION_BY (trade_year), COMPRESSION ZSTD)
                """
            )
        summary = self.verify()
        files = sorted(path for path in snapshot_dir.rglob("*") if path.is_file())
        checksums = {
            str(path.relative_to(snapshot_dir)): _sha256(path)
            for path in files
        }
        manifest = {
            "version": version,
            "created_at": datetime.now(_CST).isoformat(),
            "source": ["eastmoney"],
            "summary": summary,
            "sha256": checksums,
        }
        (snapshot_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return snapshot_dir


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
