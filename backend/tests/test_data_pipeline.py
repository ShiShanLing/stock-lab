from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_lab.data_sources import DailyBar, StockIdentity
from stock_lab.data_sources.eastmoney import _date_text
from stock_lab.warehouse import MarketWarehouse


class MarketWarehouseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.warehouse = MarketWarehouse(Path(self.temp.name) / "market")
        self.stock = StockIdentity(
            secid="1.600000",
            code="600000",
            name="测试银行",
            market=1,
            list_date="1999-11-10",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_catalog_daily_resume_verify_and_export(self) -> None:
        self.assertEqual(self.warehouse.upsert_stocks([self.stock]), 1)
        pending = self.warehouse.stocks_for_sync("20180101", "20260923")
        self.assertEqual([stock.secid for stock in pending], ["1.600000"])

        self.warehouse.mark_sync_started(self.stock.secid, "20180101", "20260923")
        bars = [
            DailyBar(
                secid=self.stock.secid,
                trade_date="2026-09-22",
                open=10,
                close=10.2,
                high=10.3,
                low=9.9,
                volume=1000,
                amount=10200,
                amplitude_pct=4,
                change_pct=2,
                change_amount=0.2,
                turnover_pct=1.1,
            ),
            DailyBar(
                secid=self.stock.secid,
                trade_date="2026-09-23",
                open=10.2,
                close=10.1,
                high=10.4,
                low=10,
                volume=900,
                amount=9090,
                amplitude_pct=3.92,
                change_pct=-0.98,
                change_amount=-0.1,
                turnover_pct=1,
            ),
        ]
        self.assertEqual(
            self.warehouse.save_daily_bars(
                self.stock.secid, bars, "20180101", "20260923"
            ),
            2,
        )
        self.assertEqual(
            self.warehouse.stocks_for_sync("20180101", "20260923"), []
        )
        verification = self.warehouse.verify()
        self.assertTrue(verification["passed"])
        self.assertEqual(verification["bar_count"], 2)

        snapshot = self.warehouse.export_snapshot("TEST_v1")
        manifest = json.loads((snapshot / "manifest.json").read_text())
        self.assertEqual(manifest["version"], "TEST_v1")
        self.assertEqual(manifest["summary"]["bar_count"], 2)
        self.assertTrue((snapshot / "stocks.parquet").exists())

    def test_eastmoney_date_parser(self) -> None:
        self.assertEqual(_date_text(20180102), "2018-01-02")
        self.assertIsNone(_date_text("-"))
        self.assertIsNone(_date_text("20181399"))

    def test_interrupted_sync_is_resumable(self) -> None:
        self.warehouse.upsert_stocks([self.stock])
        self.warehouse.mark_sync_started(self.stock.secid, "20180101", "20260923")

        pending = self.warehouse.stocks_for_sync("20180101", "20260923")
        self.assertEqual([stock.secid for stock in pending], ["1.600000"])
        self.assertEqual(
            self.warehouse.stocks_for_sync(
                "20180101", "20260923", retry_failed=False
            ),
            [],
        )

    def test_adjustment_mode_cannot_be_mixed(self) -> None:
        self.warehouse.ensure_daily_adjustment("qfq")
        self.warehouse.ensure_daily_adjustment("qfq")
        with self.assertRaises(ValueError):
            self.warehouse.ensure_daily_adjustment("none")


if __name__ == "__main__":
    unittest.main()
