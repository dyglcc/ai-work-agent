"""提醒调度服务：管理用户定时提醒."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Reminder:
    id: str
    user_id: str
    content: str
    trigger_at: float  # unix timestamp
    created_at: float = field(default_factory=time.time)
    triggered: bool = False
    read: bool = False


class ReminderService:
    """内存中的提醒管理服务."""

    def __init__(self) -> None:
        self._reminders: dict[str, Reminder] = {}  # id -> Reminder
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """启动后台检查任务."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._check_loop())
            logger.info("提醒服务已启动")

    def stop(self) -> None:
        """停止后台任务."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("提醒服务已停止")

    def add_reminder(self, user_id: str, minutes: float, content: str) -> Reminder:
        """添加一个提醒."""
        reminder = Reminder(
            id=uuid.uuid4().hex[:12],
            user_id=user_id,
            content=content,
            trigger_at=time.time() + minutes * 60,
        )
        self._reminders[reminder.id] = reminder
        logger.info("添加提醒: user=%s, minutes=%.1f, content=%s", user_id, minutes, content)
        return reminder

    def get_pending(self, user_id: str) -> list[Reminder]:
        """获取用户待触发的提醒."""
        now = time.time()
        return [
            r for r in self._reminders.values()
            if r.user_id == user_id and not r.triggered and r.trigger_at > now
        ]

    def get_triggered(self, user_id: str) -> list[Reminder]:
        """获取已触发但未读的提醒."""
        return [
            r for r in self._reminders.values()
            if r.user_id == user_id and r.triggered and not r.read
        ]

    def mark_read(self, reminder_ids: list[str]) -> None:
        """标记提醒为已读."""
        for rid in reminder_ids:
            if rid in self._reminders:
                self._reminders[rid].read = True

    async def _check_loop(self) -> None:
        """后台循环：每 10 秒检查一次是否有提醒需要触发."""
        try:
            while True:
                await asyncio.sleep(10)
                now = time.time()
                for reminder in self._reminders.values():
                    if not reminder.triggered and reminder.trigger_at <= now:
                        reminder.triggered = True
                        logger.info("提醒触发: user=%s, content=%s", reminder.user_id, reminder.content)

                # 清理已读超过 1 小时的提醒
                expired = [
                    rid for rid, r in self._reminders.items()
                    if r.read and (now - r.trigger_at) > 3600
                ]
                for rid in expired:
                    del self._reminders[rid]

        except asyncio.CancelledError:
            pass


# 全局单例
reminder_service = ReminderService()
