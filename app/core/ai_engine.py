from __future__ import annotations

import logging
import time
from collections import OrderedDict

import anthropic
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 会话历史条目
_HistoryEntry = dict  # {"role": str, "content": str}


class ConversationMemory:
    """按用户管理多轮会话历史."""

    def __init__(self, max_turns: int = 20, max_users: int = 500, ttl: int = 3600) -> None:
        self._history: OrderedDict[str, list[_HistoryEntry]] = OrderedDict()
        self._timestamps: dict[str, float] = {}
        self._max_turns = max_turns
        self._max_users = max_users
        self._ttl = ttl  # 会话过期时间（秒）

    def _cleanup(self) -> None:
        now = time.time()
        expired = [uid for uid, ts in self._timestamps.items() if now - ts > self._ttl]
        for uid in expired:
            self._history.pop(uid, None)
            self._timestamps.pop(uid, None)

    def get(self, user_id: str) -> list[_HistoryEntry]:
        self._cleanup()
        return list(self._history.get(user_id, []))

    def add(self, user_id: str, role: str, content: str) -> None:
        self._cleanup()
        if user_id not in self._history:
            self._history[user_id] = []
        self._history[user_id].append({"role": role, "content": content})
        self._timestamps[user_id] = time.time()
        # 限制历史长度
        if len(self._history[user_id]) > self._max_turns * 2:
            self._history[user_id] = self._history[user_id][-self._max_turns * 2 :]
        # 限制用户数
        while len(self._history) > self._max_users:
            oldest_uid, _ = self._history.popitem(last=False)
            self._timestamps.pop(oldest_uid, None)

    def clear(self, user_id: str) -> None:
        self._history.pop(user_id, None)
        self._timestamps.pop(user_id, None)

    def recall_last(self, user_id: str) -> bool:
        """撤回最后一轮对话（用户消息 + AI 回复），返回是否成功"""
        if user_id not in self._history:
            return False
        history = self._history[user_id]
        if len(history) < 2:
            return False
        # 移除最后两条消息（user + assistant）
        if history[-2]["role"] == "user" and history[-1]["role"] == "assistant":
            self._history[user_id] = history[:-2]
            return True
        # 如果最后只有一条消息（可能还没收到回复），也删除
        if history[-1]["role"] == "user":
            self._history[user_id] = history[:-1]
            return True
        return False


class AIEngine:
    """Claude API 异步封装，支持多轮会话."""

    def __init__(self) -> None:
        self.provider = settings.ai_provider.lower()
        self.client = None
        self.model = settings.openai_model if self.provider == "openai" else settings.claude_model
        if self.provider == "anthropic":
            client_kwargs: dict = {"api_key": settings.anthropic_api_key}
            if settings.anthropic_base_url:
                client_kwargs["base_url"] = settings.anthropic_base_url
            self.client = anthropic.AsyncAnthropic(**client_kwargs)
        self.memory = ConversationMemory()

    async def _chat_openai_compatible(
        self,
        messages: list[_HistoryEntry],
        system_prompt: str = "",
    ) -> str:
        api_key = settings.openai_api_key or settings.anthropic_api_key
        base_url = settings.openai_base_url.rstrip("/")
        payload_messages = []
        if system_prompt:
            payload_messages.append({"role": "system", "content": system_prompt})
        payload_messages.extend(messages)

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": payload_messages,
                    "max_tokens": 4096,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    async def chat(
        self,
        user_message: str,
        system_prompt: str = "",
        user_id: str = "",
    ) -> str:
        """发送消息给 Claude 并返回回复文本. 传入 user_id 可启用多轮会话."""
        try:
            # 构建消息列表
            if user_id:
                history = self.memory.get(user_id)
                messages = history + [{"role": "user", "content": user_message}]
            else:
                messages = [{"role": "user", "content": user_message}]

            if self.provider == "openai":
                reply_text = await self._chat_openai_compatible(messages, system_prompt)
            else:
                kwargs: dict = {
                    "model": self.model,
                    "max_tokens": 4096,
                    "messages": messages,
                }
                if system_prompt:
                    kwargs["system"] = system_prompt

                response = await self.client.messages.create(**kwargs)
                reply_text = response.content[0].text

            # 保存会话历史
            if user_id:
                self.memory.add(user_id, "user", user_message)
                self.memory.add(user_id, "assistant", reply_text)

            return reply_text
        except anthropic.APIError as e:
            logger.error("Claude API error: %s", e)
            return f"AI 服务暂时不可用，请稍后重试。({e.message})"
        except Exception as e:
            logger.exception("Unexpected error calling Claude API")
            return f"处理消息时出现错误，请稍后重试。({e})"
