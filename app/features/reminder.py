"""提醒功能模块：解析用户的提醒请求."""

from __future__ import annotations

from app.core.message import UnifiedMessage
from app.features.base import Feature

SYSTEM_PROMPT = """\
你是一个提醒助手。用户会请求你设置提醒/闹钟/定时任务。

你需要从用户的消息中提取：
1. 多少分钟后提醒（转换为分钟数）
2. 提醒的内容

然后严格按照以下 JSON 格式输出，不要输出任何其他内容：

```json
{"minutes": <数字>, "content": "<提醒内容>", "summary": "<对用户的确认回复>"}
```

示例：
- 用户："30分钟后提醒我开会" → {"minutes": 30, "content": "开会", "summary": "好的，30分钟后提醒你开会"}
- 用户："1小时后提醒我发邮件" → {"minutes": 60, "content": "发邮件", "summary": "好的，1小时后提醒你发邮件"}
- 用户："明天上午9点提醒我交报告" → {"minutes": 960, "content": "交报告", "summary": "好的，大约16小时后提醒你交报告（注意：我只能设置相对时间提醒）"}
- 用户："5分钟后叫我起来" → {"minutes": 5, "content": "起来", "summary": "好的，5分钟后提醒你起来"}
- 用户："半小时后提醒喝水" → {"minutes": 30, "content": "喝水", "summary": "好的，30分钟后提醒你喝水"}

只输出 JSON，不要有任何多余文字。
"""


class ReminderFeature(Feature):
    name = "提醒"
    keywords = ["提醒", "提醒我", "闹钟", "定时", "分钟后提醒", "小时后提醒", "后提醒"]
    system_prompt = SYSTEM_PROMPT

    async def handle(self, message: UnifiedMessage) -> str:
        reply = await self.ai.chat(message.content, self.system_prompt, user_id=message.user_id)
        # 标记返回，让 main.py 识别并处理
        return f"__REMINDER_JSON__\n{reply}"
