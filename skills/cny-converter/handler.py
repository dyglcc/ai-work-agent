from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime


RATE_URL = "https://open.er-api.com/v6/latest/USD"


def _parse_amount(text: str) -> float | None:
    cleaned = text.replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", cleaned)
    if not match:
        return None
    return float(match.group(0))


def _is_cny_to_usd(text: str) -> bool:
    return bool(re.search(r"人民币.*(?:美元|美金|USD)|CNY.*USD|RMB.*USD|转美元|换美元", text, re.I))


def _fetch_rate() -> tuple[float, str]:
    with urllib.request.urlopen(RATE_URL, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("result") != "success":
        raise RuntimeError("汇率接口返回失败")
    rate = float(data["rates"]["CNY"])
    updated = data.get("time_last_update_utc") or ""
    return rate, updated


async def handle(message, context):
    text = message.content.strip()
    amount = _parse_amount(text)
    if amount is None:
        return "我没识别到要换算的金额。你可以说：算一下 35，或 35 美元换人民币。"

    rate, updated = _fetch_rate()
    reverse = _is_cny_to_usd(text)
    source = "open.er-api.com"
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
