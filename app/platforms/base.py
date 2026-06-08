from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.message import Platform, ReplyMessage, UnifiedMessage


class PlatformAdapter(ABC):
    """平台适配器抽象基类."""

    platform: Platform  # 子类必须声明所属平台

    @abstractmethod
    async def start(self) -> None:
        """启动平台连接."""

    @abstractmethod
    async def stop(self) -> None:
        """关闭平台连接."""

    @abstractmethod
    async def send_reply(self, reply: ReplyMessage) -> None:
        """发送回复消息到对应平台."""

    @abstractmethod
    def parse_message(self, raw: dict) -> UnifiedMessage | None:
        """将平台原始消息解析为统一消息模型. 返回 None 表示忽略该消息."""
