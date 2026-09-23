from __future__ import annotations

import re

from .models import Condition, NaturalLanguageResult, ScreenRule, SortRule


PRESETS: dict[str, dict[str, str]] = {
    "低估值": {"text": "排除ST、停牌和上市不足一年的股票，市盈率低于20，市净率低于3，按市盈率从低到高取前10只。"},
    "放量突破": {"text": "排除ST和新股，选择近20日上涨且近5日成交量放大1.2倍的股票，按近20日涨幅排序取前10只。"},
    "均线多头": {"text": "排除ST、停牌和新股，选择均线多头排列的股票，按近20日涨幅从高到低取前10只。"},
    "近期强势": {"text": "排除ST和新股，选择近20日涨幅大于5%的股票，按近20日涨幅从高到低取前10只。"},
    "低波动": {"text": "排除ST、停牌和新股，选择近20日年化波动低于25%的股票，按波动率从低到高取前10只。"},
}


def _number(text: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    return None


def parse_natural_language(text: str) -> NaturalLanguageResult:
    normalized = text.replace("％", "%").replace("，", ",")
    conditions: list[Condition] = []
    warnings: list[str] = []

    pe_max = _number(normalized, [r"市盈率(?:低于|小于|不超过)\s*(\d+(?:\.\d+)?)", r"PE\s*[<＜]\s*(\d+(?:\.\d+)?)"])
    if pe_max is not None:
        conditions.append(Condition(field="pe", operator="<", value=pe_max, label="市盈率"))

    pb_max = _number(normalized, [r"市净率(?:低于|小于|不超过)\s*(\d+(?:\.\d+)?)", r"PB\s*[<＜]\s*(\d+(?:\.\d+)?)"])
    if pb_max is not None:
        conditions.append(Condition(field="pb", operator="<", value=pb_max, label="市净率"))

    cap_min = _number(normalized, [r"市值(?:高于|大于|不少于)\s*(\d+(?:\.\d+)?)\s*亿"])
    if cap_min is not None:
        conditions.append(Condition(field="market_cap_yi", operator=">", value=cap_min, label="总市值(亿)"))
    cap_max = _number(normalized, [r"市值(?:低于|小于|不超过)\s*(\d+(?:\.\d+)?)\s*亿"])
    if cap_max is not None:
        conditions.append(Condition(field="market_cap_yi", operator="<", value=cap_max, label="总市值(亿)"))

    amount_min = _number(normalized, [r"成交额(?:高于|大于|不少于)\s*(\d+(?:\.\d+)?)\s*亿"])
    if amount_min is not None:
        conditions.append(Condition(field="amount_yi", operator=">", value=amount_min, label="成交额(亿)"))

    change_min = _number(normalized, [r"(?:近20日|近一个月)(?:涨幅)?(?:高于|大于|超过)\s*(\d+(?:\.\d+)?)\s*%"])
    if change_min is not None:
        conditions.append(Condition(field="change_20d", operator=">", value=change_min, label="近20日涨幅(%)"))
    elif "近20日上涨" in normalized or "近一个月上涨" in normalized:
        conditions.append(Condition(field="change_20d", operator=">", value=0, label="近20日涨幅(%)"))

    volume_ratio = _number(normalized, [r"(?:成交量)?(?:放大|量比)(?:达到|高于)?\s*(\d+(?:\.\d+)?)\s*倍"])
    if volume_ratio is not None:
        conditions.append(Condition(field="volume_ratio_5d", operator=">=", value=volume_ratio, label="近5日量比"))
    elif "放量" in normalized:
        conditions.append(Condition(field="volume_ratio_5d", operator=">=", value=1.2, label="近5日量比"))

    volatility_max = _number(normalized, [r"(?:年化)?波动(?:率)?(?:低于|小于|不超过)\s*(\d+(?:\.\d+)?)\s*%"])
    if volatility_max is not None:
        conditions.append(Condition(field="volatility_20d", operator="<", value=volatility_max, label="近20日年化波动(%)"))

    if "均线多头" in normalized or "多头排列" in normalized:
        conditions.append(Condition(field="ma_bullish", operator="==", value=True, label="均线多头"))

    sort = SortRule(field="change_20d", direction="desc")
    if "市盈率从低" in normalized or "市盈率升序" in normalized:
        sort = SortRule(field="pe", direction="asc")
    elif "波动率从低" in normalized or "低波动" in normalized:
        sort = SortRule(field="volatility_20d", direction="asc")
    elif "成交额" in normalized and ("排序" in normalized or "从高" in normalized):
        sort = SortRule(field="amount_yi", direction="desc")

    limit_match = re.search(r"(?:前|取)\s*(\d+)\s*只", normalized)
    limit = min(100, max(1, int(limit_match.group(1)))) if limit_match else 20
    min_days = 365 if any(word in normalized for word in ["新股", "上市不足一年", "上市不满一年"]) else 0

    if not conditions:
        warnings.append("没有识别到明确的数值或技术条件，当前仅执行基础排除与排序。")

    rule = ScreenRule(
        exclude_st="不排除ST" not in normalized,
        exclude_suspended="不排除停牌" not in normalized,
        min_listed_days=min_days,
        conditions=conditions,
        sort=sort,
        limit=limit,
    )
    return NaturalLanguageResult(
        summary=f"已识别 {len(conditions)} 个筛选条件，最多返回 {limit} 只股票。",
        rule=rule,
        warnings=warnings,
    )

