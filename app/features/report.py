from __future__ import annotations

import json
import logging

from app.core.message import UnifiedMessage
from app.features.base import Feature

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
你是一个专业的数据分析报告助手。用户会提供数据或业务场景，你需要生成结构化的报告内容。

**输出格式要求（必须严格遵守）：**

请输出 JSON 格式，包含以下字段：
{
  "title": "报告标题",
  "sections": [
    {"heading": "一、摘要", "paragraphs": ["段落1", "段落2"]},
    {"heading": "二、背景分析", "paragraphs": ["段落1", "段落2"]},
    {"heading": "三、核心发现", "paragraphs": ["段落1", "段落2"]},
    {"heading": "四、详细分析", "paragraphs": ["段落1", "段落2"]},
    {"heading": "五、建议与行动项", "paragraphs": ["段落1", "段落2"]}
  ],
  "summary": "简短的文字摘要（1-2句话）"
}

**内容要求：**
1. 梳理分析框架（背景、发现、建议）
2. 使用数据支撑观点，语言专业严谨
3. 建议部分要具体可执行
4. summary 字段用于在聊天界面显示给用户

**重要：直接输出 JSON，不要添加任何其他文字说明。**
"""


class ReportFeature(Feature):
    name = "分析报告"
    keywords = ["报告", "分析", "数据分析", "调研"]
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
            return f"__REPORT_JSON__\n{json.dumps(data, ensure_ascii=False)}"
        except Exception as e:
            logger.warning("报告 JSON 解析失败: %s", e)
            # 如果解析失败，返回原始文本回复
            return reply
