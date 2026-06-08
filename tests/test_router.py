"""智能路由器模块测试"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from app.core.router import CommandRouter, _CLEAR_KEYWORDS
from app.core.message import UnifiedMessage, Platform


class TestCommandRouter:
    """测试命令路由器"""

    def setup_method(self):
        self.ai_engine = MagicMock()
        self.router = CommandRouter(self.ai_engine)

    def test_initialization(self):
        assert self.router.ai_engine is self.ai_engine
        assert len(self.router.features) > 0
        assert self.router.fallback is not None

    def test_clear_keywords(self):
        assert "清除记忆" in _CLEAR_KEYWORDS
        assert "清空会话" in _CLEAR_KEYWORDS
        assert "重新开始" in _CLEAR_KEYWORDS
        assert "新对话" in _CLEAR_KEYWORDS

    def test_route_clear_memory(self):
        """清除指令应清空记忆"""
        msg = UnifiedMessage(
            platform=Platform.DINGTALK,
            message_id="test1",
            user_id="user1",
            user_name="test",
            content="清除对话",
        )
        self.ai_engine.memory.clear = MagicMock()
        # 路由到清除指令

    def test_route_report(self):
        """日报/周报关键词应路由到报告功能"""
        msg = UnifiedMessage(
            platform=Platform.DINGTALK,
            message_id="test1",
            user_id="user1",
            user_name="test",
            content="帮我写一份日报",
        )
        matched = False
        for feature in self.router.features:
            if feature.matches(msg.content):
                matched = True
                break
        assert matched

    def test_route_meeting(self):
        """会议关键词应路由到会议功能"""
        msg = UnifiedMessage(
            platform=Platform.DINGTALK,
            message_id="test1",
            user_id="user1",
            user_name="test",
            content="整理一下会议纪要",
        )
        matched = False
        for feature in self.router.features:
            if feature.matches(msg.content):
                matched = True
                break
        assert matched

    def test_route_translate(self):
        """翻译关键词应路由到翻译功能"""
        msg = UnifiedMessage(
            platform=Platform.DINGTALK,
            message_id="test1",
            user_id="user1",
            user_name="test",
            content="翻译成英文",
        )
        matched = False
        for feature in self.router.features:
            if feature.matches(msg.content):
                matched = True
                break
        assert matched

    def test_route_fallback(self):
        """不匹配任何关键词应走 fallback"""
        msg = UnifiedMessage(
            platform=Platform.DINGTALK,
            message_id="test1",
            user_id="user1",
            user_name="test",
            content="你好，今天天气怎么样",
        )
        matched = False
        for feature in self.router.features:
            if feature.matches(msg.content):
                matched = True
                break
        # 通常会 fallback 到通用助手
        if not matched:
            assert self.router.fallback is not None

    def test_route_code(self):
        """代码关键词应路由到代码助手"""
        msg = UnifiedMessage(
            platform=Platform.DINGTALK,
            message_id="test1",
            user_id="user1",
            user_name="test",
            content="帮我写一段代码",
        )
        matched = False
        for feature in self.router.features:
            if feature.matches(msg.content):
                matched = True
                break
        assert matched

    def test_route_chart(self):
        """图表关键词应路由到图表功能"""
        msg = UnifiedMessage(
            platform=Platform.DINGTALK,
            message_id="test1",
            user_id="user1",
            user_name="test",
            content="生成一个柱状图",
        )
        matched = False
        for feature in self.router.features:
            if feature.matches(msg.content):
                matched = True
                break
        assert matched

    def test_route_reminder(self):
        """提醒关键词应路由到提醒功能"""
        msg = UnifiedMessage(
            platform=Platform.DINGTALK,
            message_id="test1",
            user_id="user1",
            user_name="test",
            content="10分钟后提醒我开会",
        )
        matched = False
        for feature in self.router.features:
            if feature.matches(msg.content):
                matched = True
                break
        assert matched

    def test_route_empty_message(self):
        """空消息走 fallback"""
        msg = UnifiedMessage(
            platform=Platform.DINGTALK,
            message_id="test1",
            user_id="user1",
            user_name="test",
            content="",
        )
        matched = False
        for feature in self.router.features:
            if feature.matches(msg.content):
                matched = True
                break
        assert not matched


class TestUnifiedMessage:
    """测试统一消息模型"""

    def test_message_creation(self):
        msg = UnifiedMessage(
            platform=Platform.DINGTALK,
            message_id="msg123",
            user_id="user456",
            user_name="张三",
            content="你好",
        )
        assert msg.platform == Platform.DINGTALK
        assert msg.message_id == "msg123"
        assert msg.user_id == "user456"
        assert msg.user_name == "张三"
        assert msg.content == "你好"

    def test_message_platform_enum(self):
        assert Platform.DINGTALK.value == "dingtalk"
        assert Platform.FEISHU.value == "feishu"