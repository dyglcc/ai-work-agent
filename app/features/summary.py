from __future__ import annotations

from app.core.message import UnifiedMessage
from app.features.base import Feature

SYSTEM_PROMPT = """\
你是一个专业的工作汇总助手。用户会提供一段时间内的工作内容，你需要：

1. 按类别整理工作内容（如：项目开发、会议沟通、文档编写等）
2. 提炼关键成果和进展
3. 标注待跟进事项
4. 输出结构清晰、简洁专业的工作汇总

输出格式要求：
- 使用分点列表
- 每个类别下列出具体事项
- 最后附上"待跟进事项"部分
- 语言简洁专业，适合发给领导或团队
"""


class SummaryFeature(Feature):
    name = "工作汇总"
    keywords = ["汇总", "总结", "周报", "日报", "月报"]
    system_prompt = SYSTEM_PROMPT

    async def handle(self, message: UnifiedMessage) -> str:
        return await self.ai.chat(message.content, self.system_prompt, user_id=message.user_id)
