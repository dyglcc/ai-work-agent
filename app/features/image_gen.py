from __future__ import annotations

import json
import logging

from app.core.message import UnifiedMessage
from app.features.base import Feature

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
你是一个 AI 图片生成助手。用户会描述想要生成的图片，你需要优化提示词并生成结构化输出。

**输出格式要求（必须严格遵守）：**

请输出 JSON 格式，包含以下字段：
{
  "prompt": "优化后的英文提示词（详细描述画面内容、风格、色彩等）",
  "summary": "简短的中文说明（告诉用户正在生成什么图片）"
}

**内容要求：**
1. prompt 必须使用英文，详细描述图片内容
2. 包含风格、色彩、构图等视觉元素
3. 提示词要具体明确，避免过于抽象
4. summary 用于在聊天界面显示给用户

**示例：**
用户输入："生成一张日落海滩的图片"
输出：
{
  "prompt": "A beautiful sunset over a tropical beach, golden hour lighting, warm orange and pink sky, calm ocean waves, palm trees silhouette, peaceful atmosphere, highly detailed, photorealistic, 4k quality",
  "summary": "正在为您生成一张日落海滩的图片..."
}

**重要：直接输出 JSON，不要添加任何其他文字说明。**
"""


class ImageGenFeature(Feature):
    name = "AI生图"
    keywords = ["生成图片", "画图", "画一个", "画一张", "生成一张", "生图", "image", "draw"]
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
            return f"__IMAGE_JSON__\n{json.dumps(data, ensure_ascii=False)}"
        except Exception as e:
            logger.warning("图片生成 JSON 解析失败: %s", e)
            return reply
