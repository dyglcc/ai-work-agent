from __future__ import annotations

import re


def _clean_query(text: str) -> str:
    query = text.strip()
    query = re.sub(r"^(帮我|请)?\s*(搜一下|搜索|查一下|查找|查查)\s*", "", query)
    return query.strip(" ：:，,") or text.strip()


async def handle(message, context):
    tools = context.get("tools", {})
    web_search = tools.get("web_search")
    if not web_search:
        return "当前没有可用的 web_search 工具。"

    query = _clean_query(message.content)
    results = await web_search(query, max_results=5)
    if not results:
        return f"没有搜索到和「{query}」相关的结果。"

    lines = [f"我搜索了：{query}", ""]
    for idx, item in enumerate(results, 1):
        title = item.get("title") or "无标题"
        snippet = item.get("snippet") or "-"
        url = item.get("url") or ""
        lines.append(f"{idx}. {title}")
        lines.append(f"   摘要：{snippet}")
        if url:
            lines.append(f"   链接：{url}")
        lines.append("")
    return "\n".join(lines).strip()
