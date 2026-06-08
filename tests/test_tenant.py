"""多租户模块测试"""

import pytest
from app.core.tenant import (
    TenantManager,
    TenantTier,
    TenantQuota,
    TenantMemoryStore,
    tenant_manager,
)


class TestTenantQuota:
    """测试配额管理"""

    def test_free_tier_quota(self):
        quota = TenantQuota.for_tier(TenantTier.FREE)
        assert quota.daily_requests == 50
        assert quota.max_rag_documents == 5
        assert "assistant" in quota.features
        assert "image_gen" not in quota.features

    def test_enterprise_tier_quota(self):
        quota = TenantQuota.for_tier(TenantTier.ENTERPRISE)
        assert quota.daily_requests == 10000
        assert quota.max_rag_documents == 1000
        assert "image_gen" in quota.features
        assert "assistant" in quota.features

    def test_pro_tier_quota(self):
        quota = TenantQuota.for_tier(TenantTier.PRO)
        assert quota.daily_requests == 1000
        assert "ppt" in quota.features
        assert "image_gen" not in quota.features


class TestTenantMemoryStore:
    """测试租户记忆存储"""

    def test_add_and_get(self):
        store = TenantMemoryStore(max_messages=10)
        store.add("user1", "user", "Hello")
        store.add("user1", "assistant", "Hi there")
        msgs = store.get("user1")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello"

    def test_max_messages_trimming(self):
        store = TenantMemoryStore(max_messages=3)
        for i in range(5):
            store.add("user1", "user", f"Message {i}")
        msgs = store.get("user1")
        assert len(msgs) == 3
        # 应该保留最近 3 条
        assert msgs[0]["content"] == "Message 2"
        assert msgs[2]["content"] == "Message 4"

    def test_clear_user(self):
        store = TenantMemoryStore()
        store.add("user1", "user", "Hello")
        store.add("user2", "user", "Hi")
        store.clear("user1")
        assert len(store.get("user1")) == 0
        assert len(store.get("user2")) == 1

    def test_recall_last(self):
        store = TenantMemoryStore()
        store.add("user1", "user", "Hello")
        store.add("user1", "assistant", "Hi")
        assert store.recall_last("user1") is True
        assert len(store.get("user1")) == 1
        assert store.get("user1")[0]["content"] == "Hello"

    def test_recall_last_empty(self):
        store = TenantMemoryStore()
        assert store.recall_last("user1") is False

    def test_clear_all(self):
        store = TenantMemoryStore()
        store.add("user1", "user", "Hello")
        store.add("user2", "user", "Hi")
        store.clear_all()
        assert len(store.get("user1")) == 0
        assert len(store.get("user2")) == 0


class TestTenantManager:
    """测试租户管理器"""

    def setup_method(self):
        self.manager = TenantManager()

    def test_create_tenant(self):
        config = self.manager.create_tenant("t1", "企业A", TenantTier.BASIC)
        assert config.tenant_id == "t1"
        assert config.name == "企业A"
        assert config.tier == TenantTier.BASIC
        assert config.is_active is True

    def test_create_duplicate_tenant(self):
        self.manager.create_tenant("t1", "企业A")
        with pytest.raises(ValueError, match="已存在"):
            self.manager.create_tenant("t1", "企业B")

    def test_get_tenant(self):
        self.manager.create_tenant("t1", "企业A")
        config = self.manager.get_tenant("t1")
        assert config is not None
        assert config.name == "企业A"

    def test_get_nonexistent_tenant(self):
        config = self.manager.get_tenant("nonexistent")
        assert config is None

    def test_get_or_create_default(self):
        # 首次调用：自动创建
        config = self.manager.get_or_create_default("new_user")
        assert config.tenant_id == "new_user"
        assert config.tier == TenantTier.FREE

        # 再次调用：返回已有
        config2 = self.manager.get_or_create_default("new_user")
        assert config2 is config

    def test_update_tenant(self):
        self.manager.create_tenant("t1", "企业A", TenantTier.FREE)
        config = self.manager.update_tenant("t1", name="企业A新名称", tier=TenantTier.PRO)
        assert config.name == "企业A新名称"
        assert config.tier == TenantTier.PRO
        assert config.quota.daily_requests == 1000

    def test_update_nonexistent_tenant(self):
        config = self.manager.update_tenant("nonexistent", name="X")
        assert config is None

    def test_delete_tenant(self):
        self.manager.create_tenant("t1", "企业A")
        assert self.manager.delete_tenant("t1") is True
        assert self.manager.get_tenant("t1") is None

    def test_delete_nonexistent_tenant(self):
        assert self.manager.delete_tenant("nonexistent") is False

    def test_list_tenants(self):
        self.manager.create_tenant("t1", "企业A", TenantTier.FREE)
        self.manager.create_tenant("t2", "企业B", TenantTier.ENTERPRISE)
        tenants = self.manager.list_tenants()
        assert len(tenants) == 2
        assert tenants[0]["tenant_id"] == "t1"
        assert tenants[1]["tier"] == "enterprise"

    def test_check_quota_chat(self):
        self.manager.create_tenant("t1", "企业A", TenantTier.FREE)
        # 免费版 50 次请求
        for i in range(50):
            ok, msg = self.manager.check_quota("t1", "chat")
            assert ok, f"第 {i+1} 次请求应该成功: {msg}"
        # 第 51 次失败
        ok, msg = self.manager.check_quota("t1", "chat")
        assert not ok
        assert "已达上限" in msg

    def test_check_quota_rag_add(self):
        self.manager.create_tenant("t1", "企业A", TenantTier.FREE)
        for i in range(5):
            ok, _ = self.manager.check_quota("t1", "rag_add")
            assert ok
        ok, msg = self.manager.check_quota("t1", "rag_add")
        assert not ok
        assert "已达上限" in msg

    def test_check_quota_workflow(self):
        self.manager.create_tenant("t1", "企业A", TenantTier.FREE)
        for i in range(5):
            ok, _ = self.manager.check_quota("t1", "workflow")
            assert ok
        ok, msg = self.manager.check_quota("t1", "workflow")
        assert not ok

    def test_check_quota_inactive_tenant(self):
        self.manager.create_tenant("t1", "企业A")
        self.manager.update_tenant("t1", is_active=False)
        ok, msg = self.manager.check_quota("t1", "chat")
        assert not ok
        assert "禁用" in msg

    def test_check_quota_auto_create(self):
        """不存在的租户自动创建免费租户"""
        ok, msg = self.manager.check_quota("auto_tenant", "chat")
        assert ok
        config = self.manager.get_tenant("auto_tenant")
        assert config is not None
        assert config.tier == TenantTier.FREE

    def test_get_feature_access(self):
        self.manager.create_tenant("t1", "企业A", TenantTier.FREE)
        features = self.manager.get_feature_access("t1")
        assert "assistant" in features
        assert "image_gen" not in features

    def test_get_feature_access_unknown_tenant(self):
        features = self.manager.get_feature_access("unknown")
        assert "assistant" in features
        assert "translate" in features

    def test_get_usage(self):
        self.manager.create_tenant("t1", "企业A")
        usage = self.manager.get_usage("t1")
        assert usage["daily_requests"] == 0

        self.manager.check_quota("t1", "chat")
        usage = self.manager.get_usage("t1")
        assert usage["daily_requests"] == 1

    def test_get_memory(self):
        self.manager.create_tenant("t1", "企业A", TenantTier.BASIC)
        memory = self.manager.get_memory("t1")
        assert memory.max_messages == 50  # basic 等级
        memory.add("user1", "user", "Hello")
        assert len(memory.get("user1")) == 1

    def test_get_memory_isolation(self):
        """不同租户的记忆隔离"""
        self.manager.create_tenant("t1", "企业A")
        self.manager.create_tenant("t2", "企业B")
        mem1 = self.manager.get_memory("t1")
        mem2 = self.manager.get_memory("t2")
        mem1.add("user1", "user", "Hello from t1")
        mem2.add("user1", "user", "Hello from t2")
        assert mem1.get("user1")[0]["content"] == "Hello from t1"
        assert mem2.get("user1")[0]["content"] == "Hello from t2"


class TestGlobalTenantManager:
    """测试全局单例"""

    def test_singleton(self):
        from app.core.tenant import get_tenant_manager
        mgr1 = get_tenant_manager()
        mgr2 = get_tenant_manager()
        assert mgr1 is mgr2