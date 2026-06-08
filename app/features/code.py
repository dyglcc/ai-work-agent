from __future__ import annotations

from app.core.message import UnifiedMessage
from app.features.base import Feature

SYSTEM_PROMPT = """\
你是一个资深的编程助手。你可以帮助用户：

1. 编写代码（Python、JavaScript、Java、Go 等主流语言）
2. 调试和修复代码问题
3. 代码审查和优化建议
4. 解释代码逻辑
5. 设计技术方案

要求：
- 代码使用 markdown 代码块格式输出
- 附上简要说明和关键注释
- 如果有多种实现方式，推荐最佳实践
- 注意安全性和性能
"""


class CodeFeature(Feature):
    name = "代码助手"
    keywords = ["代码", "编程", "code", "bug", "debug", "函数", "接口", "API"]
    system_prompt = SYSTEM_PROMPT

    async def handle(self, message: UnifiedMessage) -> str:
        return await self.ai.chat(message.content, self.system_prompt, user_id=message.user_id)
