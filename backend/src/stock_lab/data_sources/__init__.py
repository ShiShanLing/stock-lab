"""外部市场数据源适配器。"""

from .eastmoney import DailyBar, EastmoneyProvider, StockIdentity
from .baostock_provider import BaoStockProvider

__all__ = ["BaoStockProvider", "DailyBar", "EastmoneyProvider", "StockIdentity"]
