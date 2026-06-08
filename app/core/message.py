from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Platform(str, Enum):
    DINGTALK = "dingtalk"
    FEISHU = "feishu"


@dataclass
class UnifiedMessage:
    """平台无关的统一消息模型."""

    platform: Platform
    message_id: str
    user_id: str
    user_name: str
    content: str  # 纯文本内容
    is_group: bool = False
    group_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)  # 原始平台数据，供回复使用


@dataclass
class ReplyMessage:
    """统一回复消息."""

    content: str
    source_message: UnifiedMessage
    files: list[dict[str, Any]] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
