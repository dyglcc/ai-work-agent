from __future__ import annotations

import json
import logging

from app.core.message import UnifiedMessage
from app.features.base import Feature

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
你是一个专业的 PPT 生成助手。用户会描述一个演示主题，你需要生成结构化的 PPT 内容。

**输出格式要求（必须严格遵守）：**

请输出 JSON 格式，包含以下字段：
{
  "title": "PPT 总标题",
  "slides": [
    {"title": "第一页标题", "content": ["要点1", "要点2", "要点3"]},
    {"title": "第二页标题", "content": ["要点1", "要点2", "要点3"]},
    ...
  ],
  "summary": "简短的文字摘要（1-2句话）"
}

**内容要求：**
1. 生成清晰的 PPT 结构（目录、各章节、总结）
2. 每页 3-5 个要点，语言简洁
3. 控制总页数在 8-15 页之间
4. summary 字段用于在聊天界面显示给用户

**重要：直接输出 JSON，不要添加任何其他文字说明。**
"""


class PPTFeature(Feature):
    name = "PPT大纲"
    keywords = ["PPT", "ppt", "幻灯片", "演示文稿", "演示"]
    system_prompt = SYSTEM_PROMPT

    async def handle(self, message: UnifiedMessage) -> str:
        reply = await self.ai.chat(message.content, self.system_prompt, user_id=message.user_id)

        # 尝试解析 JSON，如果失败则返回原始回复
        try:
            # 提取 JSON（可能包含在 ```json 代码块中）
            json_str = reply.strip()
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0].strip()
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0].strip()

            data = json.loads(json_str)

            # 返回带有特殊标记的 JSON，供 main.py 处理
            return f"__PPT_JSON__\n{json.dumps(data, ensure_ascii=False)}"
        except Exception as e:
            logger.warning("PPT JSON 解析失败: %s", e)
            # 如果解析失败，返回原始文本回复
            return reply
