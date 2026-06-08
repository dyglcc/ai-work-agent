from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Coroutine

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from app.config import settings
from app.core.message import Platform, ReplyMessage, UnifiedMessage
from app.platforms.base import PlatformAdapter

logger = logging.getLogger(__name__)

FEISHU_FILE_TYPES = {
    "pptx": "ppt",
    "ppt": "ppt",
    "docx": "doc",
    "doc": "doc",
    "xlsx": "xls",
    "xls": "xls",
    "pdf": "pdf",
}


class FeishuAdapter(PlatformAdapter):
    """飞书 WebSocket 模式适配器."""

    platform = Platform.FEISHU

    def __init__(self) -> None:
        self._on_message: Callable[[UnifiedMessage], Coroutine[Any, Any, None]] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        # 飞书事件处理器
        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_message_event)
            .build()
        )

        # WebSocket 客户端（event_handler 在构造时传入）
        self._ws_client = lark.ws.Client(
            settings.feishu_app_id,
            settings.feishu_app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        # API 客户端用于回复消息
        self._api_client = (
            lark.Client.builder()
            .app_id(settings.feishu_app_id)
            .app_secret(settings.feishu_app_secret)
            .build()
        )

    def set_message_handler(
        self, handler: Callable[[UnifiedMessage], Coroutine[Any, Any, None]]
    ) -> None:
        self._on_message = handler

    def _handle_message_event(self, data: P2ImMessageReceiveV1) -> None:
        """飞书消息事件回调（在 SDK 线程中调用）."""
        try:
            event = data.event
            msg = event.message

            # 只处理文本消息
            if msg.message_type != "text":
                return

            content_dict = json.loads(msg.content)
            text = content_dict.get("text", "").strip()
            if not text:
                return

            sender = event.sender
            open_id = sender.sender_id.open_id if sender.sender_id else ""

            unified = UnifiedMessage(
                platform=Platform.FEISHU,
                message_id=msg.message_id or "",
                user_id=open_id,
                user_name=open_id,  # 飞书需要额外 API 获取用户名，此处先用 open_id
                content=text,
                is_group=msg.chat_type == "group",
                group_id=msg.chat_id or "",
                raw={
                    "message_id": msg.message_id,
                    "chat_id": msg.chat_id,
                    "chat_type": msg.chat_type,
                    "open_id": open_id,
                },
            )

            if self._on_message and self._loop:
                asyncio.run_coroutine_threadsafe(self._on_message(unified), self._loop)
        except Exception:
            logger.exception("处理飞书消息失败")

    async def start(self) -> None:
        logger.info("启动飞书 WebSocket 连接...")
        self._loop = asyncio.get_event_loop()
        self._loop.run_in_executor(None, self._start_ws_client)

    def _start_ws_client(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        import lark_oapi.ws.client as ws_client

        ws_client.loop = loop
        self._ws_client.start()

    async def stop(self) -> None:
        logger.info("关闭飞书 WebSocket 连接...")

    async def send_reply(self, reply: ReplyMessage) -> None:
        """通过飞书 API 回复消息."""
        raw = reply.source_message.raw
        message_id = raw.get("message_id", "")

        if not message_id:
            logger.warning("飞书消息缺少 message_id，无法回复")
            return

        body = (
            ReplyMessageRequestBody.builder()
            .content(json.dumps({"text": reply.content}))
            .msg_type("text")
            .build()
        )

        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: self._api_client.im.v1.message.reply(request)
        )

        if not response.success():
            logger.error(
                "飞书回复失败: code=%s msg=%s",
                response.code,
                response.msg,
            )
            return

        logger.info("飞书回复已发送: message_id=%s", message_id)

        for file_info in reply.files:
            await self._send_file_message(reply.source_message, file_info)

    async def _send_file_message(self, source: UnifiedMessage, file_info: dict[str, Any]) -> None:
        file_path = Path(str(file_info.get("path", "")))
        if not file_path.is_file():
            logger.warning("飞书文件不存在，跳过上传: %s", file_path)
            return

        file_key = await self._upload_file(file_path, str(file_info.get("name") or file_path.name))
        if not file_key:
            return

        raw = source.raw
        receive_id = raw.get("chat_id") or raw.get("open_id")
        receive_id_type = "chat_id" if raw.get("chat_id") else "open_id"
        if not receive_id:
            logger.warning("飞书消息缺少 receive_id，无法发送文件")
            return

        body = (
            CreateMessageRequestBody.builder()
            .receive_id(receive_id)
            .msg_type("file")
            .content(json.dumps({"file_key": file_key}, ensure_ascii=False))
            .build()
        )
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(body)
            .build()
        )

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: self._api_client.im.v1.message.create(request)
        )
        if not response.success():
            logger.error(
                "飞书文件消息发送失败: code=%s msg=%s",
                response.code,
                response.msg,
            )
            return

        logger.info("飞书文件消息已发送: file=%s file_key=%s", file_info.get("name"), file_key)

    async def _upload_file(self, file_path: Path, file_name: str) -> str | None:
        extension = file_path.suffix.lower().lstrip(".")
        file_type = FEISHU_FILE_TYPES.get(extension, "stream")

        def upload() -> Any:
            with file_path.open("rb") as file_obj:
                body = (
                    CreateFileRequestBody.builder()
                    .file_type(file_type)
                    .file_name(file_name)
                    .file(file_obj)
                    .build()
                )
                request = CreateFileRequest.builder().request_body(body).build()
                return self._api_client.im.v1.file.create(request)

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, upload)
        if not response.success():
            logger.error(
                "飞书文件上传失败: code=%s msg=%s file=%s",
                response.code,
                response.msg,
                file_name,
            )
            return None

        file_key = response.data.file_key if response.data else ""
        if not file_key:
            logger.error("飞书文件上传成功但未返回 file_key: file=%s", file_name)
            return None
        logger.info("飞书文件上传成功: file=%s file_key=%s", file_name, file_key)
        return file_key

    def parse_message(self, raw: dict) -> UnifiedMessage | None:
        try:
            return UnifiedMessage(
                platform=Platform.FEISHU,
                message_id=raw.get("message_id", ""),
                user_id=raw.get("open_id", ""),
                user_name=raw.get("open_id", ""),
                content=raw.get("text", ""),
                is_group=raw.get("chat_type") == "group",
                group_id=raw.get("chat_id", ""),
                raw=raw,
            )
        except Exception:
            logger.exception("解析飞书消息失败")
            return None
