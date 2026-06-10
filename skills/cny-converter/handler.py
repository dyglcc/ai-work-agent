from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime
from typing import Any


RATE_URL = "https://open.er-api.com/v6/latest/USD"


def _parse_amount(text: str) -> float | None:
    cleaned = text.replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", cleaned)
    if not match:
        return None
    return float(match.group(0))


def _is_cny_to_usd(text: str) -> bool:
    return bool(re.search(r"人民币.*(?:美元|美金|USD)|CNY.*USD|RMB.*USD|转美元|换美元", text, re.I))


def _rate_from_search_results(results: list[dict[str, str]]) -> float | None:
    text = " ".join(
        f"{item.get('title', '')} {item.get('snippet', '')}"
        for item in results
    )
    patterns = [
        r"1\s*(?:USD|US Dollar|美元).*?([67]\.\d{2,5})\s*(?:CNY|RMB|人民币|Chinese Yuan)",
        r"([67]\.\d{2,5})\s*(?:CNY|RMB|人民币|Chinese Yuan)",
        r"美元兑人民币.*?([67]\.\d{2,5})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return float(match.group(1))
    return None


async def _fetch_rate_from_tools(tools: dict[str, Any]) -> tuple[float | None, str, str]:
    web_search = tools.get("web_search") if tools else None
    if not web_search:
        return None, "", ""
    results = await web_search("USD to CNY exchange rate today 美元 人民币 汇率", max_results=5)
    rate = _rate_from_search_results(results)
    if rate is None:
        return None, "", ""
    source = results[0].get("url", "web_search") if results else "web_search"
    return rate, source, "实时搜索"


def _fetch_rate_api() -> tuple[float, str, str]:
    with urllib.request.urlopen(RATE_URL, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("result") != "success":
        raise RuntimeError("汇率接口返回失败")
    rate = float(data["rates"]["CNY"])
    updated = data.get("time_last_update_utc") or ""
    return rate, "open.er-api.com", updated


async def handle(message, context):
    text = message.content.strip()
    amount = _parse_amount(text)
    if amount is None:
        return "我没识别到要换算的金额。你可以说：算一下 35，或 35 美元换人民币。"

    rate = None
    source = ""
    updated = ""
    try:
        rate, source, updated = await _fetch_rate_from_tools(context.get("tools", {}))
    except Exception:
        rate = None
    if rate is None:
        rate, source, updated = _fetch_rate_api()
    reverse = _is_cny_to_usd(text)
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if reverse:
        result = amount / rate
        return (
            f"当前汇率：1 USD = {rate:.4f} CNY（来源：{source}，更新时间：{updated or checked_at}）\n"
            f"¥{amount:,.2f} ≈ ${result:,.2f}"
        )

    result = amount * rate
    return (
        f"当前汇率：1 USD = {rate:.4f} CNY（来源：{source}，更新时间：{updated or checked_at}）\n"
        f"${amount:,.2f} ≈ ¥{result:,.2f}"
    )
