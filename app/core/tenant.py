"""多租户支持模块：租户隔离、多企业配置、配额管理"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.core.ai_engine import AIEngine

logger = logging.getLogger(__name__)


class TenantTier(str, Enum):
    """租户等级"""
    FREE = "free"
    BASIC = "basic"
    PRO = "pro"
    ENTERPRISE = "enterprise"


@dataclass
class TenantQuota:
    """租户配额"""
    daily_requests: int = 100
    max_memory_messages: int = 50
    max_rag_documents: int = 10
    max_workflow_executions: int = 20
    max_file_upload_mb: int = 10
    features: set[str] = field(default_factory=lambda: {"assistant", "translate", "summary"})

    @classmethod
    def for_tier(cls, tier: TenantTier) -> "TenantQuota":
        """根据等级返回默认配额"""
        quotas = {
            TenantTier.FREE: cls(
                daily_requests=50,
                max_memory_messages=20,
                max_rag_documents=5,
                max_workflow_executions=5,
                max_file_upload_mb=5,
                features={"assistant", "translate", "summary"},
            ),
            TenantTier.BASIC: cls(
                daily_requests=200,
                max_memory_messages=50,
                max_rag_documents=20,
                max_workflow_executions=20,
                max_file_upload_mb=10,
                features={"assistant", "translate", "summary", "report", "meeting", "code", "email"},
            ),
            TenantTier.PRO: cls(
                daily_requests=1000,
                max_memory_messages=100,
                max_rag_documents=100,
                max_workflow_executions=100,
                max_file_upload_mb=50,
                features={"assistant", "translate", "summary", "report", "meeting", "code", "email", "chart", "reminder", "ppt"},
            ),
            TenantTier.ENTERPRISE: cls(
                daily_requests=10000,
                max_memory_messages=500,
                max_rag_documents=1000,
                max_workflow_executions=1000,
                max_file_upload_mb=200,
                features={"assistant", "translate", "summary", "report", "meeting", "code", "email", "chart", "reminder", "ppt", "image_gen"},
            ),
        }
        return quotas.get(tier, cls())


@dataclass
class TenantUsage:
    """租户当日用量"""
    daily_requests: int = 0
    rag_documents: int = 0
    workflow_executions: int = 0
    last_reset: float = field(default_factory=time.time)


@dataclass
class TenantConfig:
    """租户配置"""
    tenant_id: str
    name: str
    tier: TenantTier = TenantTier.FREE
    quota: TenantQuota = field(default_factory=TenantQuota)
    ai_model: str = ""
    ai_api_key: str = ""
    custom_settings: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    is_active: bool = True

    def __post_init__(self):
        if not self.ai_model or not self.ai_api_key:
            # 使用全局默认配置
            from app.config import settings
            self.ai_model = self.ai_model or settings.claude_model
            self.ai_api_key = self.ai_api_key or settings.anthropic_api_key


class TenantMemoryStore:
    """租户隔离的对话记忆存储"""

    def __init__(self, max_messages: int = 50):
        self.max_messages = max_messages
        self._store: dict[str, list[dict]] = {}
        self._lock = threading.RLock()

    def add(self, user_id: str, role: str, content: str) -> None:
        with self._lock:
            if user_id not in self._store:
                self._store[user_id] = []
            self._store[user_id].append({"role": role, "content": content})
            # 保留最近 N 条
            if len(self._store[user_id]) > self.max_messages:
                self._store[user_id] = self._store[user_id][-self.max_messages:]

    def get(self, user_id: str) -> list[dict]:
        with self._lock:
            return list(self._store.get(user_id, []))

    def clear(self, user_id: str) -> None:
        with self._lock:
            self._store.pop(user_id, None)

    def recall_last(self, user_id: str) -> bool:
        with self._lock:
            msgs = self._store.get(user_id, [])
            if msgs:
                msgs.pop()
                return True
            return False

    def clear_all(self) -> None:
        with self._lock:
            self._store.clear()


class TenantManager:
    """多租户管理器"""

    def __init__(self):
        self._tenants: dict[str, TenantConfig] = {}
        self._usage: dict[str, TenantUsage] = {}
        self._memories: dict[str, TenantMemoryStore] = {}
        self._lock = threading.RLock()

    def create_tenant(
        self,
        tenant_id: str,
        name: str,
        tier: TenantTier = TenantTier.FREE,
        ai_model: str = "",
        ai_api_key: str = "",
        custom_settings: dict | None = None,
    ) -> TenantConfig:
        """创建租户"""
        with self._lock:
            if tenant_id in self._tenants:
                raise ValueError(f"租户 {tenant_id} 已存在")

            quota = TenantQuota.for_tier(tier)
            config = TenantConfig(
                tenant_id=tenant_id,
                name=name,
                tier=tier,
                quota=quota,
                ai_model=ai_model,
                ai_api_key=ai_api_key,
                custom_settings=custom_settings or {},
            )
            self._tenants[tenant_id] = config
            self._usage[tenant_id] = TenantUsage()
            self._memories[tenant_id] = TenantMemoryStore(max_messages=quota.max_memory_messages)
            logger.info("创建租户: %s (%s, %s)", tenant_id, name, tier.value)
            return config

    def get_tenant(self, tenant_id: str) -> TenantConfig | None:
        """获取租户配置"""
        return self._tenants.get(tenant_id)

    def get_or_create_default(self, tenant_id: str) -> TenantConfig:
        """获取租户，不存在则创建默认（免费）租户"""
        with self._lock:
            if tenant_id not in self._tenants:
                return self.create_tenant(tenant_id, tenant_id, TenantTier.FREE)
            return self._tenants[tenant_id]

    def update_tenant(self, tenant_id: str, **kwargs) -> TenantConfig | None:
        """更新租户配置"""
        with self._lock:
            config = self._tenants.get(tenant_id)
            if not config:
                return None
            for key, value in kwargs.items():
                if hasattr(config, key):
                    setattr(config, key, value)
            # 如果等级变更，更新配额
            if "tier" in kwargs:
                config.quota = TenantQuota.for_tier(config.tier)
                # 更新记忆存储上限
                if tenant_id in self._memories:
                    self._memories[tenant_id].max_messages = config.quota.max_memory_messages
            return config

    def delete_tenant(self, tenant_id: str) -> bool:
        """删除租户"""
        with self._lock:
            if tenant_id not in self._tenants:
                return False
            del self._tenants[tenant_id]
            self._usage.pop(tenant_id, None)
            self._memories.pop(tenant_id, None)
            logger.info("删除租户: %s", tenant_id)
            return True

    def list_tenants(self) -> list[dict]:
        """列出所有租户"""
        with self._lock:
            return [
                {
                    "tenant_id": t.tenant_id,
                    "name": t.name,
                    "tier": t.tier.value,
                    "is_active": t.is_active,
                    "quota": {
                        "daily_requests": t.quota.daily_requests,
                        "max_memory_messages": t.quota.max_memory_messages,
                        "max_rag_documents": t.quota.max_rag_documents,
                        "max_workflow_executions": t.quota.max_workflow_executions,
                        "max_file_upload_mb": t.quota.max_file_upload_mb,
                        "features": list(t.quota.features),
                    },
                    "created_at": t.created_at,
                }
                for t in self._tenants.values()
            ]

    def get_usage(self, tenant_id: str) -> dict:
        """获取租户用量"""
        with self._lock:
            usage = self._usage.get(tenant_id)
            if not usage:
                return {"daily_requests": 0, "rag_documents": 0, "workflow_executions": 0}
            # 检查是否需要重置当日用量
            if time.time() - usage.last_reset > 86400:  # 24小时
                usage.daily_requests = 0
                usage.workflow_executions = 0
                usage.last_reset = time.time()
            return {
                "daily_requests": usage.daily_requests,
                "rag_documents": usage.rag_documents,
                "workflow_executions": usage.workflow_executions,
                "last_reset": usage.last_reset,
            }

    def check_quota(self, tenant_id: str, action: str) -> tuple[bool, str]:
        """检查配额是否允许操作"""
        with self._lock:
            config = self._tenants.get(tenant_id)
            if not config:
                # 自动创建免费租户
                config = self.create_tenant(tenant_id, tenant_id, TenantTier.FREE)
            if not config.is_active:
                return False, "租户已被禁用"

            usage = self._usage.get(tenant_id)
            if not usage:
                usage = TenantUsage()
                self._usage[tenant_id] = usage

            # 重置当日用量
            if time.time() - usage.last_reset > 86400:
                usage.daily_requests = 0
                usage.workflow_executions = 0
                usage.last_reset = time.time()

            if action == "chat":
                if usage.daily_requests >= config.quota.daily_requests:
                    return False, f"当日请求已达上限 ({config.quota.daily_requests})"
                usage.daily_requests += 1

            elif action == "rag_add":
                if usage.rag_documents >= config.quota.max_rag_documents:
                    return False, f"知识库文档已达上限 ({config.quota.max_rag_documents})"
                usage.rag_documents += 1

            elif action == "workflow":
                if usage.workflow_executions >= config.quota.max_workflow_executions:
                    return False, f"工作流执行次数已达上限 ({config.quota.max_workflow_executions})"
                usage.workflow_executions += 1

            elif action == "feature":
                feature_name = config.custom_settings.get("check_feature", "")
                if feature_name and feature_name not in config.quota.features:
                    return False, f"当前等级不支持功能: {feature_name}"

            return True, "ok"

    def get_feature_access(self, tenant_id: str) -> set[str]:
        """获取租户可用的功能列表"""
        config = self._tenants.get(tenant_id)
        if not config:
            return {"assistant", "translate", "summary"}
        return config.quota.features

    def get_memory(self, tenant_id: str) -> TenantMemoryStore:
        """获取租户隔离的记忆存储"""
        with self._lock:
            if tenant_id not in self._memories:
                config = self._tenants.get(tenant_id)
                max_msgs = config.quota.max_memory_messages if config else 50
                self._memories[tenant_id] = TenantMemoryStore(max_messages=max_msgs)
            return self._memories[tenant_id]


# 全局单例
tenant_manager = TenantManager()


def get_tenant_manager() -> TenantManager:
    return tenant_manager
