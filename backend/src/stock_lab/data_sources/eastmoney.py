from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from datetime import date, datetime
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import requests


_UT = "bd1d9ddb04089700cf9c27f6f7426281"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
_LIST_HOSTS = [
    "https://39.push2.eastmoney.com",
    "https://48.push2.eastmoney.com",
    "https://push2.eastmoney.com",
]
_HISTORY_HOSTS = [
    "https://push2his.eastmoney.com",
    "https://41.push2his.eastmoney.com",
    "https://45.push2his.eastmoney.com",
]
_A_SHARE_FILTER = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
_A_SHARE_FILTER_ENCODED = quote(_A_SHARE_FILTER, safe="")


@dataclass(frozen=True)
class StockIdentity:
    secid: str
    code: str
    name: str
    market: int
    list_date: str | None
    source: str = "eastmoney"


@dataclass(frozen=True)
class DailyBar:
    secid: str
    trade_date: str
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float
    amplitude_pct: float
    change_pct: float
    change_amount: float
    turnover_pct: float
    source: str = "eastmoney"


def _number(value: str) -> float:
    if value in {"", "-", None}:
        return 0.0
    return float(value)


def _date_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        try:
            return datetime.strptime(text, "%Y%m%d").date().isoformat()
        except ValueError:
            return None
    return None


class EastmoneyProvider:
    def __init__(
        self,
        timeout: float = 25,
        retries: int = 6,
        adjustment: str = "qfq",
    ) -> None:
        adjustments = {"none": 0, "qfq": 1, "hfq": 2}
        if adjustment not in adjustments:
            raise ValueError(f"不支持的复权方式：{adjustment}")
        self.timeout = timeout
        self.retries = retries
        self.adjustment = adjustment
        self.adjustment_code = adjustments[adjustment]

    def _request_json(
        self,
        session: requests.Session,
        hosts: list[str],
        path: str,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            ordered_hosts = hosts[attempt % len(hosts) :] + hosts[: attempt % len(hosts)]
            for host in ordered_hosts:
                try:
                    response = session.get(
                        f"{host}{path}", headers=_HEADERS, timeout=self.timeout
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("rc") == 0 and payload.get("data") is not None:
                        return payload
                    raise RuntimeError(f"数据源返回异常 rc={payload.get('rc')}")
                except Exception as exc:
                    last_error = exc
                    session.close()
            time.sleep(min(8, 0.7 * 2**attempt) + random.random() * 0.4)
        raise RuntimeError(f"东方财富接口连续失败：{last_error}")

    async def fetch_stock_catalog(
        self,
        on_page: Callable[[list[StockIdentity]], None] | None = None,
    ) -> list[StockIdentity]:
        return await asyncio.to_thread(self._fetch_stock_catalog_sync, on_page)

    def _fetch_stock_catalog_sync(
        self,
        on_page: Callable[[list[StockIdentity]], None] | None = None,
    ) -> list[StockIdentity]:
        stocks: dict[str, StockIdentity] = {}
        with requests.Session() as session:
            page = 1
            total = 1
            while len(stocks) < total:
                path = (
                    "/api/qt/clist/get?fltt=2&invt=2&np=1&po=0"
                    f"&pn={page}&pz=100&fid=f12&fs={_A_SHARE_FILTER_ENCODED}"
                    f"&fields=f12,f13,f14,f26&ut={_UT}"
                )
                try:
                    payload = self._request_json(session, _LIST_HOSTS, path)
                except Exception as exc:
                    raise RuntimeError(f"股票目录第 {page} 页获取失败：{exc}") from exc
                data = payload.get("data") or {}
                total = int(data.get("total") or 0)
                rows = data.get("diff") or []
                if not rows:
                    break
                page_stocks: list[StockIdentity] = []
                for row in rows:
                    code = str(row.get("f12") or "")
                    market = int(row.get("f13") or 0)
                    if not code:
                        continue
                    secid = f"{market}.{code}"
                    identity = StockIdentity(
                        secid=secid,
                        code=code,
                        name=str(row.get("f14") or code),
                        market=market,
                        list_date=_date_text(row.get("f26")),
                    )
                    stocks[secid] = identity
                    page_stocks.append(identity)
                if on_page:
                    on_page(page_stocks)
                page += 1
                if page % 10 == 1:
                    print(f"股票目录进度：已读取 {len(stocks)}/{total}", flush=True)
                time.sleep(0.8 + random.random() * 0.4)
        return sorted(stocks.values(), key=lambda stock: stock.secid)

    def fetch_daily_bars(
        self,
        session: requests.Session,
        stock: StockIdentity,
        start_date: str = "20180101",
        end_date: str | None = None,
    ) -> list[DailyBar]:
        end = end_date or date.today().strftime("%Y%m%d")
        path = (
            f"/api/qt/stock/kline/get?klt=101&fqt={self.adjustment_code}"
            f"&secid={stock.secid}&beg={start_date}&end={end}"
            "&fields1=f1,f2,f3,f4,f5,f6,f7,f8"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        )
        payload = self._request_json(session, _HISTORY_HOSTS, path)
        data = payload.get("data") or {}
        bars: list[DailyBar] = []
        for line in data.get("klines") or []:
            parts = str(line).split(",")
            if len(parts) < 11:
                continue
            bars.append(
                DailyBar(
                    secid=stock.secid,
                    trade_date=parts[0],
                    open=_number(parts[1]),
                    close=_number(parts[2]),
                    high=_number(parts[3]),
                    low=_number(parts[4]),
                    volume=_number(parts[5]),
                    amount=_number(parts[6]),
                    amplitude_pct=_number(parts[7]),
                    change_pct=_number(parts[8]),
                    change_amount=_number(parts[9]),
                    turnover_pct=_number(parts[10]),
                    source=f"eastmoney_{self.adjustment}",
                )
            )
        return bars
