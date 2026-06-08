from __future__ import annotations

import logging

from app.core.ai_engine import AIEngine
from app.core.message import UnifiedMessage
from app.features.assistant import AssistantFeature
from app.features.base import Feature
from app.features.chart import ChartFeature
from app.features.code import CodeFeature
from app.features.email import EmailFeature
from app.features.image_gen import ImageGenFeature
from app.features.meeting import MeetingFeature
from app.features.ppt import PPTFeature
from app.features.reminder import ReminderFeature
from app.features.report import ReportFeature
from app.features.summary import SummaryFeature
from app.features.translate import TranslateFeature

logger = logging.getLogger(__name__)

_CLEAR_KEYWORDS = {"清除记忆", "清空会话", "重新开始", "新对话"}


class CommandRouter:
    """命令路由器：根据关键词将消息分发到对应的功能模块."""

    def __init__(self, ai_engine: AIEngine) -> None:
        self.ai_engine = ai_engine
        self.features: list[Feature] = [
            SummaryFeature(ai_engine),
            PPTFeature(ai_engine),
            ReportFeature(ai_engine),
            ChartFeature(ai_engine),
            ImageGenFeature(ai_engine),
            TranslateFeature(ai_engine),
            EmailFeature(ai_engine),
            ReminderFeature(ai_engine),
            MeetingFeature(ai_engine),
            CodeFeature(ai_engine),
        ]
        self.fallback = AssistantFeature(ai_engine)

    async def route(self, message: UnifiedMessage) -> str:
        """路由消息到匹配的功能模块并返回回复."""
        # 处理清除会话的指令
        content = message.content.strip()
        if content in _CLEAR_KEYWORDS:
            self.ai_engine.memory.clear(message.user_id)
            return "会话已清空，我们可以重新开始对话了。"

        for feature in self.features:
            if feature.matches(content):
                logger.info("消息路由到: %s", feature.name)
                return await feature.handle(message)
        logger.info("消息路由到: %s (兜底)", self.fallback.name)
        return await self.fallback.handle(message)
