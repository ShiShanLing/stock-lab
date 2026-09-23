from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from stock_lab.data_sources import DailyBar, StockIdentity
from stock_lab.research import candidate_strategies, run_research
from stock_lab.warehouse import MarketWarehouse


class StrategyResearchTest(unittest.TestCase):
    def test_walk_forward_report_has_locked_blind_test_finalists(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            warehouse = MarketWarehouse(root / "market")
            cursor = date(2018, 1, 1)
            dates: list[date] = []
            while cursor <= date(2026, 9, 23):
                if cursor.weekday() < 5:
                    dates.append(cursor)
                cursor += timedelta(days=1)

            for index in range(8):
                stock = StockIdentity(
                    secid=f"1.6000{index:02d}",
                    code=f"6000{index:02d}",
                    name=f"研究样本{index}",
                    market=1,
                    list_date="2010-01-01",
                )
                warehouse.upsert_stocks([stock])
                warehouse.mark_sync_started(stock.secid, "20180101", "20260923")
                bars: list[DailyBar] = []
                price = 10.0 + index
                for day_index, trading_date in enumerate(dates):
                    change = 0.0003 + index * 0.00008 + math.sin(day_index / 19) * 0.003
                    open_price = price
                    price = max(1, price * (1 + change))
                    bars.append(
                        DailyBar(
                            secid=stock.secid,
                            trade_date=trading_date.isoformat(),
                            open=open_price,
                            close=price,
                            high=max(open_price, price) * 1.01,
                            low=min(open_price, price) * 0.99,
                            volume=2_000_000 + index * 100_000,
                            amount=price * (2_000_000 + index * 100_000),
                            amplitude_pct=2,
                            change_pct=change * 100,
                            change_amount=price - open_price,
                            turnover_pct=1.5,
                        )
                    )
                warehouse.save_daily_bars(stock.secid, bars, "20180101", "20260923")

            path = run_research(warehouse.database_path, root / "research")
            report = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(report["methodology"]["candidate_count"], 94)
            self.assertEqual(len(report["finalists"]), 3)
            self.assertEqual(
                set(report["finalists"]), set(report["results"]["blind_test"])
            )
            self.assertTrue(candidate_strategies())


if __name__ == "__main__":
    unittest.main()
