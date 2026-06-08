from __future__ import annotations

from app.core.message import UnifiedMessage
from app.features.base import Feature

SYSTEM_PROMPT = """\
你是一个专业翻译助手。请根据用户的需求进行翻译：

- 如果用户输入中文，默认翻译成英文
- 如果用户输入英文或其他外语，默认翻译成中文
- 用户可以指定目标语言（如"翻译成日文"）
- 保持原文的语气和风格
- 对于专业术语，在括号中附上原文
- 如果原文有歧义，提供多种翻译并说明区别
"""


class TranslateFeature(Feature):
    name = "翻译"
    keywords = ["翻译", "translate", "英译中", "中译英"]
    system_prompt = SYSTEM_PROMPT

    async def handle(self, message: UnifiedMessage) -> str:
        return await self.ai.chat(message.content, self.system_prompt, user_id=message.user_id)
