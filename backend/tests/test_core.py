from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from stock_lab.api import create_api
from stock_lab.backtest import run_backtest
from stock_lab.models import BacktestRequest
from stock_lab.repository import Repository
from stock_lab.screening import run_screen
from stock_lab.skill import parse_natural_language


class StockLabTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repository = Repository(Path(self.temp.name) / "test.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_natural_language_to_screen_results(self) -> None:
        parsed = parse_natural_language(
            "排除ST、停牌和新股，市盈率低于30，近20日上涨，按近20日涨幅排序取前5只"
        )
        self.assertEqual(parsed.rule.limit, 5)
        self.assertEqual(parsed.rule.min_listed_days, 365)
        self.assertEqual(len(parsed.rule.conditions), 2)
        result = run_screen(self.repository, parsed.rule)
        self.assertLessEqual(result.matched_count, 5)
        self.assertTrue(all(stock.pe < 30 for stock in result.stocks))
        self.assertTrue(all(stock.change_20d > 0 for stock in result.stocks))

    def test_backtest_is_repeatable_and_returns_metrics(self) -> None:
        parsed = parse_natural_language("排除ST和新股，市盈率低于35，按近20日涨幅排序取前5只")
        request = BacktestRequest(rule=parsed.rule, position_count=3, rebalance_days=20)
        first = run_backtest(self.repository, request)
        second = run_backtest(self.repository, request)
        self.assertGreater(first.metrics.periods, 5)
        self.assertEqual(first.metrics.total_return_pct, second.metrics.total_return_pct)
        self.assertEqual(first.curve, second.curve)
        self.assertTrue(first.notes)

    def test_api_full_flow(self) -> None:
        client = TestClient(create_api(self.repository))
        self.assertEqual(client.get("/health").status_code, 200)
        parsed = client.post(
            "/api/skill/parse",
            json={"text": "排除ST和新股，选择均线多头的股票，取前5只"},
        )
        self.assertEqual(parsed.status_code, 200)
        rule = parsed.json()["rule"]
        screened = client.post("/api/screen", json={"rule": rule})
        self.assertEqual(screened.status_code, 200)

        saved = client.post(
            "/api/strategies",
            json={"name": "测试策略", "description": "接口测试", "rule": rule},
        )
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(len(client.get("/api/strategies").json()), 1)

        backtest = client.post(
            "/api/backtests",
            json={"rule": rule, "position_count": 3, "rebalance_days": 20},
        )
        self.assertEqual(backtest.status_code, 201)
        self.assertIn("total_return_pct", backtest.json()["metrics"])


if __name__ == "__main__":
    unittest.main()

