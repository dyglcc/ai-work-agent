from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus, unquote, urlparse, parse_qs

import httpx

logger = logging.getLogger(__name__)


@dataclass
class WebSearchResult:
    title: str
    url: str
    snippet: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


class ToolRegistry:
    """项目内置工具注册表，供 Skill handler 和提示型 Skill 使用."""

    def __init__(self) -> None:
        self._tools = {
            "web_search": web_search,
            "web_fetch": web_fetch,
        }

    def as_dict(self) -> dict[str, Any]:
        return dict(self._tools)

    async def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if name not in self._tools:
            raise ValueError(f"未知工具: {name}")
        return await self._tools[name](*args, **kwargs)


async def web_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """使用公开搜索页执行轻量 Web 搜索，返回标题、链接和摘要."""
    query = query.strip()
    if not query:
        return []
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AIWorkAgent/0.1; +https://localhost)",
    }
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
    return [item.to_dict() for item in _parse_duckduckgo_html(response.text, max_results)]


async def web_fetch(url: str, max_chars: int = 4000) -> str:
    """抓取网页并抽取粗略正文，供 Skill 使用."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("仅支持 http/https URL")
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AIWorkAgent/0.1; +https://localhost)",
    }
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
    text = _html_to_text(response.text)
    return text[:max_chars]


def format_search_results(results: list[dict[str, str]]) -> str:
    if not results:
        return "未搜索到可用结果。"
    lines: list[str] = []
    for idx, item in enumerate(results, 1):
        title = item.get("title", "").strip() or "无标题"
        url = item.get("url", "").strip()
        snippet = item.get("snippet", "").strip()
        lines.append(f"{idx}. {title}\n   URL: {url}\n   摘要: {snippet or '-'}")
    return "\n".join(lines)


def _parse_duckduckgo_html(text: str, max_results: int) -> list[WebSearchResult]:
    results: list[WebSearchResult] = []
    blocks = re.split(r'<div class="result results_links[^"]*"', text)
    for block in blocks[1:]:
        link_match = re.search(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not link_match:
            continue
        raw_url = html.unescape(link_match.group(1))
        title = _html_to_text(link_match.group(2))
        snippet_match = re.search(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        snippet = _html_to_text(snippet_match.group(1)) if snippet_match else ""
        normalized_url = _normalize_ddg_url(raw_url)
        if not title or not normalized_url:
            continue
        results.append(WebSearchResult(title=title, url=normalized_url, snippet=snippet))
        if len(results) >= max_results:
            break
    return results


def _normalize_ddg_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg")
        if uddg:
            return unquote(uddg[0])
    return url


def _html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


tool_registry = ToolRegistry()
