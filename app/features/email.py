from __future__ import annotations

from app.core.message import UnifiedMessage
from app.features.base import Feature

SYSTEM_PROMPT = """\
你是一个专业的邮件撰写助手。用户会描述邮件目的和背景，你需要：

1. 生成格式规范的邮件（包含主题行、称呼、正文、结尾、署名）
2. 语气根据场景调整（正式/半正式/轻松）
3. 结构清晰，重点突出
4. 如果用户没有指定，默认使用正式商务语气

输出格式：
- 邮件主题：xxx
- 正文内容（完整可复制）
"""


class EmailFeature(Feature):
    name = "邮件撰写"
    keywords = ["邮件", "email", "写信", "发信"]
    system_prompt = SYSTEM_PROMPT

    async def handle(self, message: UnifiedMessage) -> str:
        return await self.ai.chat(message.content, self.system_prompt, user_id=message.user_id)
