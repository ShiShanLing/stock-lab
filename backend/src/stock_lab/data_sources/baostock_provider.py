from __future__ import annotations

from typing import Any

import baostock as bs
import requests

from .eastmoney import DailyBar, StockIdentity


def _baostock_code(stock: StockIdentity) -> str:
    if stock.code.startswith("6"):
        prefix = "sh"
    elif stock.code.startswith(("4", "8", "9")):
        prefix = "bj"
    else:
        prefix = "sz"
    return f"{prefix}.{stock.code}"


def _float(value: str | None) -> float:
    return float(value) if value not in {None, ""} else 0.0


class BaoStockProvider:
    """BaoStock顺序补偿源；官方客户端使用全局会话，因此不并发查询。"""

    supports_parallel = False

    def __init__(self, adjustment: str = "qfq") -> None:
        adjustments = {"none": "3", "qfq": "2", "hfq": "1"}
        if adjustment not in adjustments:
            raise ValueError(f"不支持的复权方式：{adjustment}")
        self.adjustment = adjustment
        self.adjustment_code = adjustments[adjustment]
        self._logged_in = False

    def open(self) -> None:
        result = bs.login()
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock登录失败：{result.error_msg}")
        self._logged_in = True

    def close(self) -> None:
        if self._logged_in:
            bs.logout()
            self._logged_in = False

    def fetch_daily_bars(
        self,
        _session: requests.Session,
        stock: StockIdentity,
        start_date: str = "20180101",
        end_date: str | None = None,
    ) -> list[DailyBar]:
        if not self._logged_in:
            raise RuntimeError("BaoStock尚未登录")
        start = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
        end_text = end_date or ""
        end = (
            f"{end_text[:4]}-{end_text[4:6]}-{end_text[6:8]}"
            if end_text
            else ""
        )
        fields = (
            "date,open,high,low,close,preclose,volume,amount,turn,"
            "tradestatus,pctChg,isST"
        )
        result = bs.query_history_k_data_plus(
            _baostock_code(stock),
            fields,
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag=self.adjustment_code,
        )
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock查询失败：{result.error_msg}")
        bars: list[DailyBar] = []
        while result.next():
            values = dict(zip(result.fields, result.get_row_data(), strict=True))
            if values.get("tradestatus") != "1":
                continue
            open_price = _float(values.get("open"))
            high = _float(values.get("high"))
            low = _float(values.get("low"))
            close = _float(values.get("close"))
            preclose = _float(values.get("preclose"))
            if min(open_price, high, low, close) <= 0:
                continue
            amplitude = (high - low) / preclose * 100 if preclose > 0 else 0.0
            bars.append(
                DailyBar(
                    secid=stock.secid,
                    trade_date=values["date"],
                    open=open_price,
                    close=close,
                    high=high,
                    low=low,
                    volume=_float(values.get("volume")),
                    amount=_float(values.get("amount")),
                    amplitude_pct=amplitude,
                    change_pct=_float(values.get("pctChg")),
                    change_amount=close - preclose if preclose > 0 else 0.0,
                    turnover_pct=_float(values.get("turn")),
                    source=f"baostock_{self.adjustment}",
                )
            )
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock读取失败：{result.error_msg}")
        return bars
