from __future__ import annotations

from app.core.message import UnifiedMessage
from app.features.base import Feature

SYSTEM_PROMPT = """\
你是一个高效的 AI 工作助手。你可以帮助用户处理各种工作相关问题，包括但不限于：

- 文案撰写和润色
- 邮件起草
- 方案策划
- 问题解答
- 翻译

请用专业、简洁的语言回复。如果需要更多信息才能给出好的回答，请主动询问。
"""


class AssistantFeature(Feature):
    name = "通用助手"
    keywords = []  # 作为兜底，不需要关键词匹配
    system_prompt = SYSTEM_PROMPT

    def matches(self, text: str) -> bool:
        return True  # 始终匹配，作为兜底

    async def handle(self, message: UnifiedMessage) -> str:
        return await self.ai.chat(message.content, self.system_prompt, user_id=message.user_id)
