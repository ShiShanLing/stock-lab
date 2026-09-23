"""外部市场数据源适配器。"""

from .eastmoney import DailyBar, EastmoneyProvider, StockIdentity

__all__ = ["DailyBar", "EastmoneyProvider", "StockIdentity"]

