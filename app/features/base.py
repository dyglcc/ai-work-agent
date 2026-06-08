from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.ai_engine import AIEngine
from app.core.message import UnifiedMessage


class Feature(ABC):
    """功能模块抽象基类."""

    name: str = ""
    keywords: list[str] = []
    system_prompt: str = ""

    def __init__(self, ai_engine: AIEngine) -> None:
        self.ai = ai_engine

    def matches(self, text: str) -> bool:
        """检查消息是否匹配本功能的关键词."""
        return any(kw in text for kw in self.keywords)

    @abstractmethod
    async def handle(self, message: UnifiedMessage) -> str:
        """处理消息并返回回复文本."""
