from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

from dingtalk_stream import AckMessage, ChatbotHandler, ChatbotMessage, Credential, DingTalkStreamClient
from dingtalk_stream.frames import CallbackMessage

from app.config import settings
from app.core.message import Platform, ReplyMessage, UnifiedMessage
from app.platforms.base import PlatformAdapter

logger = logging.getLogger(__name__)


class _BotHandler(ChatbotHandler):
    """钉钉 Stream 消息回调处理器."""

    def __init__(self) -> None:
        super().__init__()
        self.on_message: Callable[[UnifiedMessage], Coroutine[Any, Any, None]] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def pre_start(self) -> None:
        self._loop = asyncio.get_event_loop()

    def process(self, callback: CallbackMessage) -> tuple:
        """处理收到的钉钉消息（在 SDK 内部线程中调用）."""
        try:
            incoming = ChatbotMessage.from_dict(callback.data)
            text = (incoming.text.content or "").strip()
            if not text:
                return AckMessage.STATUS_OK, "ok"

            msg = UnifiedMessage(
                platform=Platform.DINGTALK,
                message_id=incoming.message_id or "",
                user_id=incoming.sender_id or "",
                user_name=incoming.sender_nick or "",
                content=text,
                is_group=incoming.conversation_type == "2",
                group_id=incoming.conversation_id or "",
                raw=callback.data,
            )

            if self.on_message and self._loop:
                asyncio.run_coroutine_threadsafe(self.on_message(msg), self._loop)

            return AckMessage.STATUS_OK, "ok"
        except Exception:
            logger.exception("处理钉钉消息失败")
            return AckMessage.STATUS_SYSTEM_EXCEPTION, "error"


class DingTalkAdapter(PlatformAdapter):
    """钉钉 Stream 模式适配器."""

    platform = Platform.DINGTALK

    def __init__(self) -> None:
        self._handler = _BotHandler()
        credential = Credential(settings.dingtalk_app_key, settings.dingtalk_app_secret)
        self._client = DingTalkStreamClient(credential)
        self._client.register_callback_handler(
            ChatbotMessage.TOPIC, self._handler
        )

    def set_message_handler(
        self, handler: Callable[[UnifiedMessage], Coroutine[Any, Any, None]]
    ) -> None:
        self._handler.on_message = handler

    async def start(self) -> None:
        logger.info("启动钉钉 Stream 连接...")
        self._handler.pre_start()
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, self._client.start_forever)

    async def stop(self) -> None:
        logger.info("关闭钉钉 Stream 连接...")

    async def send_reply(self, reply: ReplyMessage) -> None:
        """通过 session webhook 回复钉钉消息."""
        import httpx

        incoming = ChatbotMessage.from_dict(reply.source_message.raw)
        webhook = incoming.session_webhook
        if not webhook:
            logger.warning("钉钉消息缺少 session_webhook，无法回复")
            return

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                webhook,
                json={"msgtype": "text", "text": {"content": reply.content}},
                timeout=10,
            )
            if resp.status_code != 200:
                logger.error("钉钉回复失败: %s %s", resp.status_code, resp.text)

    def parse_message(self, raw: dict) -> UnifiedMessage | None:
        try:
            incoming = ChatbotMessage.from_dict(raw)
            text = (incoming.text.content or "").strip()
            if not text:
                return None
            return UnifiedMessage(
                platform=Platform.DINGTALK,
                message_id=incoming.message_id or "",
                user_id=incoming.sender_id or "",
                user_name=incoming.sender_nick or "",
                content=text,
                is_group=incoming.conversation_type == "2",
                group_id=incoming.conversation_id or "",
                raw=raw,
            )
        except Exception:
            logger.exception("解析钉钉消息失败")
            return None
