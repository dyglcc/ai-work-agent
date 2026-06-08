"""RAG 知识库模块测试"""

import pytest
from app.services.rag import (
    RAGService,
    Document,
    SearchResult,
    rag_service,
)


class TestDocument:
    """测试文档模型"""

    def test_document_creation(self):
        doc = Document(
            id="doc1",
            content="这是测试内容",
            title="测试文档",
            source="test",
        )
        assert doc.id == "doc1"
        assert doc.content == "这是测试内容"
        assert doc.title == "测试文档"

    def test_document_with_metadata(self):
        doc = Document(
            id="doc1",
            content="内容",
            title="标题",
            metadata={"author": "test", "page": 1},
        )
        assert doc.metadata["author"] == "test"
        assert doc.metadata["page"] == 1


class TestRAGService:
    """测试 RAG 服务（不使用嵌入 API，只测本地操作）"""

    def setup_method(self):
        self.service = RAGService()

    def test_list_documents(self):
        self.service.clear()
        # 直接注入文档到内部存储
        self.service._documents["d1"] = Document(id="d1", content="文档1", title="A")
        self.service._documents["d2"] = Document(id="d2", content="文档2", title="B")
        docs = self.service.list_documents()
        assert len(docs) == 2

    def test_remove_document(self):
        self.service.clear()
        self.service._documents["d1"] = Document(id="d1", content="测试", title="X")
        self.service._chunks = ["chunk1"]
        self.service._chunk_to_doc = ["d1"]
        assert self.service.remove_document("d1") is True
        assert "d1" not in self.service._documents

    def test_remove_nonexistent_document(self):
        assert self.service.remove_document("nonexistent") is False

    def test_clear(self):
        self.service._documents["d1"] = Document(id="d1", content="文档1", title="A")
        self.service._documents["d2"] = Document(id="d2", content="文档2", title="B")
        self.service._chunks = ["c1", "c2"]
        self.service._chunk_to_doc = ["d1", "d2"]
        self.service.clear()
        assert len(self.service.list_documents()) == 0
        assert len(self.service._chunks) == 0

    def test_get_stats(self):
        self.service.clear()
        self.service._documents["d1"] = Document(id="d1", content="Hello World", title="Doc1")
        self.service._chunks = ["Hello World"]
        self.service._chunk_to_doc = ["d1"]
        stats = self.service.get_stats()
        assert stats["document_count"] == 1
        assert stats["total_chars"] > 0
        assert stats["chunk_count"] >= 1

    def test_get_stats_empty(self):
        self.service.clear()
        stats = self.service.get_stats()
        assert stats["document_count"] == 0
        assert stats["total_chars"] == 0

    def test_chunk_text_short(self):
        """短文本无需分块"""
        chunks = self.service._chunk_text("Hello World")
        assert len(chunks) == 1
        assert chunks[0] == "Hello World"

    def test_chunk_text_long(self):
        """长文本应被分块"""
        self.service._chunk_size = 50
        self.service._chunk_overlap = 10
        long_text = "ABCDEFGHIJ" * 20  # 200 chars
        chunks = self.service._chunk_text(long_text)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c) <= 50

    def test_search_empty_index(self):
        self.service.clear()
        results = self.service._chunks
        assert len(results) == 0


class TestGlobalRAGService:
    """测试全局单例"""

    def test_singleton(self):
        from app.services.rag import rag_service as rs1
        from app.services.rag import rag_service as rs2
        assert rs1 is rs2
