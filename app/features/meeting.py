from __future__ import annotations

from app.core.message import UnifiedMessage
from app.features.base import Feature

SYSTEM_PROMPT = """\
你是一个专业的会议纪要整理助手。用户会提供会议内容（可能是笔记、录音转文字等），你需要分析并生成结构化的会议纪要。

严格按照以下 JSON 格式输出，不要输出任何其他内容：

```json
{
  "title": "会议标题",
  "key_points": ["重点1", "重点2", "重点3"],
  "action_items": [
    {"task": "具体任务", "owner": "负责人", "deadline": "截止时间"}
  ],
  "suggestions": ["建议1", "建议2"],
  "summary": "简短摘要（2-3句话概括会议核心内容）"
}
```

要求：
1. title：从内容中提炼会议主题
2. key_points：列出3-7个关键讨论要点和决策
3. action_items：提取所有行动项，如果没有明确负责人或截止时间，填"待定"
4. suggestions：基于会议内容给出1-3条建议
5. summary：用2-3句话概括会议核心内容

只输出 JSON，不要有任何多余文字。
"""


class MeetingFeature(Feature):
    name = "会议纪要"
    keywords = ["会议", "纪要", "会议记录", "会议总结", "meeting", "录制会议", "开始会议", "会议录音"]
    system_prompt = SYSTEM_PROMPT

    async def handle(self, message: UnifiedMessage) -> str:
        reply = await self.ai.chat(message.content, self.system_prompt, user_id=message.user_id)
        return f"__MEETING_JSON__\n{reply}"
