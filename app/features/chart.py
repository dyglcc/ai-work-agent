from __future__ import annotations

import json
import logging

from app.core.message import UnifiedMessage
from app.features.base import Feature

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
你是一个专业的数据可视化助手。用户会描述数据或图表需求，你需要生成结构化的图表数据。

**输出格式要求（必须严格遵守）：**

请输出 JSON 格式，包含以下字段：
{
  "chart_type": "bar",  // 图表类型：bar（柱状图）、line（折线图）、pie（饼图）
  "title": "图表标题",
  "data": {
    "labels": ["类别1", "类别2", "类别3"],  // X 轴标签或饼图标签
    "values": [100, 150, 200]  // 对应的数值
  },
  "summary": "简短的文字说明（1-2句话）"
}

**内容要求：**
1. 根据用户需求选择合适的图表类型
2. labels 和 values 数组长度必须相同
3. 数值型数据用数字，不要用字符串
4. summary 字段用于在聊天界面显示给用户

**重要：直接输出 JSON，不要添加任何其他文字说明。**
"""


class ChartFeature(Feature):
    name = "数据图表"
    keywords = ["图表", "柱状图", "饼图", "折线图", "chart", "可视化", "画图表"]
    system_prompt = SYSTEM_PROMPT

    async def handle(self, message: UnifiedMessage) -> str:
        reply = await self.ai.chat(message.content, self.system_prompt, user_id=message.user_id)

        # 尝试解析 JSON
        try:
            # 提取 JSON（可能包含在 ```json 代码块中）
            json_str = reply.strip()
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0].strip()
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0].strip()

            data = json.loads(json_str)

            # 返回带有特殊标记的 JSON，供 main.py 处理
            return f"__CHART_JSON__\n{json.dumps(data, ensure_ascii=False)}"
        except Exception as e:
            logger.warning("图表 JSON 解析失败: %s", e)
            return reply
