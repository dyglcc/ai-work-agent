"""RAG 知识库服务：文档索引、向量检索、增强回答"""
from __future__ import annotations

import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

# 嵌入向量维度（OpenAI text-embedding-3-small 默认 1536）
EMBEDDING_DIM = 1536


@dataclass
class Document:
    """知识库文档"""
    id: str
    content: str
    title: str = ""
    source: str = ""
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    """检索结果"""
    document: Document
    score: float
    chunk_index: int
    chunk_text: str


class RAGService:
    """RAG 知识库服务：嵌入 + 向量检索 + 上下文增强"""

    def __init__(self):
        self._documents: dict[str, Document] = {}
        self._chunks: list[str] = []  # 所有分块文本
        self._chunk_to_doc: list[str] = []  # chunk -> doc_id 映射
        self._embeddings: np.ndarray | None = None  # [N, D] 嵌入矩阵
        self._embedding_url: str = ""
        self._embedding_key: str = ""
        self._embedding_model: str = "text-embedding-3-small"
        self._chunk_size: int = 500
        self._chunk_overlap: int = 50
        self._initialized: bool = False

    def _init_embedding_config(self) -> None:
        """初始化嵌入 API 配置"""
        base = settings.anthropic_base_url.rstrip("/") if settings.anthropic_base_url else ""
        if base:
            if "/anthropic" in base:
                base = base.split("/anthropic")[0]
        self._embedding_url = settings.rag_embedding_url or f"{base}/v1/embeddings"
        self._embedding_key = settings.rag_embedding_key or settings.anthropic_api_key
        self._embedding_model = settings.rag_embedding_model or "text-embedding-3-small"
        self._chunk_size = settings.rag_chunk_size or 500
        self._chunk_overlap = settings.rag_chunk_overlap or 50
        self._initialized = True

    def _ensure_init(self) -> None:
        if not self._initialized:
            self._init_embedding_config()

    def _chunk_text(self, text: str) -> list[str]:
        """将文本分割为重叠的块"""
        if len(text) <= self._chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + self._chunk_size
            chunk = text[start:end]
            chunks.append(chunk)
            start += self._chunk_size - self._chunk_overlap
        return chunks

    async def _get_embedding(self, text: str) -> list[float]:
        """调用嵌入 API 获取文本向量"""
        self._ensure_init()

        if not self._embedding_key:
            raise ValueError("未配置嵌入 API Key，请设置 RAG_EMBEDDING_KEY 或 ANTHROPIC_API_KEY")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    self._embedding_url,
                    headers={
                        "Authorization": f"Bearer {self._embedding_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._embedding_model,
                        "input": text,
                    },
                )

            if resp.status_code == 200:
                result = resp.json()
                return result["data"][0]["embedding"]
            else:
                logger.error("嵌入 API 错误: %s %s", resp.status_code, resp.text[:500])
                raise ValueError(f"嵌入 API 调用失败 ({resp.status_code})")
        except Exception as e:
            logger.exception("调用嵌入 API 失败")
            raise

    async def _get_embeddings_batch(self, texts: list[str]) -> np.ndarray:
        """批量获取嵌入向量（逐个调用，简单实现）"""
        embeddings = []
        for text in texts:
            emb = await self._get_embedding(text)
            embeddings.append(emb)
        return np.array(embeddings, dtype=np.float32)

    def _cosine_similarity(self, query_vec: np.ndarray, doc_vecs: np.ndarray) -> np.ndarray:
        """计算余弦相似度"""
        query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
        doc_norms = doc_vecs / (np.linalg.norm(doc_vecs, axis=1, keepdims=True) + 1e-10)
        return np.dot(doc_norms, query_norm)

    # ---- 公开 API ----

    async def add_document(
        self,
        content: str,
        title: str = "",
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        添加文档到知识库

        Args:
            content: 文档内容
            title: 文档标题
            source: 来源标识
            metadata: 附加元数据

        Returns:
            文档 ID
        """
        self._ensure_init()

        doc_id = uuid.uuid4().hex[:12]
        doc = Document(
            id=doc_id,
            content=content,
            title=title,
            source=source,
            metadata=metadata or {},
        )
        self._documents[doc_id] = doc

        # 分块
        chunks = self._chunk_text(content)
        if not chunks:
            logger.warning("文档 %s 内容为空，跳过索引", doc_id)
            return doc_id

        # 获取嵌入
        try:
            chunk_embeddings = await self._get_embeddings_batch(chunks)
        except Exception:
            logger.exception("获取文档 %s 嵌入向量失败", doc_id)
            del self._documents[doc_id]
            raise

        # 追加到全局索引
        start_idx = len(self._chunks)
        self._chunks.extend(chunks)
        self._chunk_to_doc.extend([doc_id] * len(chunks))

        if self._embeddings is None:
            self._embeddings = chunk_embeddings
        else:
            self._embeddings = np.vstack([self._embeddings, chunk_embeddings])

        logger.info(
            "已添加文档 %s (%s)：%d 个分块，索引总数 %d",
            doc_id, title or "未命名", len(chunks), len(self._chunks),
        )
        return doc_id

    async def add_documents(
        self,
        documents: list[dict[str, Any]],
    ) -> list[str]:
        """
        批量添加文档

        Args:
            documents: 文档列表，每个元素包含 content, title, source, metadata 字段

        Returns:
            文档 ID 列表
        """
        doc_ids = []
        for doc_data in documents:
            doc_id = await self.add_document(
                content=doc_data["content"],
                title=doc_data.get("title", ""),
                source=doc_data.get("source", ""),
                metadata=doc_data.get("metadata"),
            )
            doc_ids.append(doc_id)
        return doc_ids

    async def search(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[SearchResult]:
        """
        检索相关文档

        Args:
            query: 查询文本
            top_k: 返回结果数量
            min_score: 最低相似度阈值

        Returns:
            检索结果列表，按相似度降序排列
        """
        self._ensure_init()

        if self._embeddings is None or len(self._chunks) == 0:
            return []

        query_vec = await self._get_embedding(query)
        query_arr = np.array(query_vec, dtype=np.float32)

        scores = self._cosine_similarity(query_arr, self._embeddings)

        # 排序并取 top_k
        top_indices = np.argsort(scores)[::-1][:top_k * 2]  # 多取一些用于去重

        results = []
        seen_docs = set()
        for idx in top_indices:
            score = float(scores[idx])
            if score < min_score:
                continue
            doc_id = self._chunk_to_doc[idx]
            if doc_id in seen_docs:
                continue
            seen_docs.add(doc_id)

            doc = self._documents[doc_id]
            results.append(SearchResult(
                document=doc,
                score=score,
                chunk_index=idx,
                chunk_text=self._chunks[idx],
            ))

            if len(results) >= top_k:
                break

        return results

    async def search_context(
        self,
        query: str,
        top_k: int = 3,
        max_tokens: int = 3000,
    ) -> str:
        """
        检索并返回格式化的上下文文本，可直接注入到 AI 提示中

        Args:
            query: 查询文本
            top_k: 检索结果数量
            max_tokens: 上下文最大 token 数（粗略估计：1 token ≈ 4 字符）

        Returns:
            格式化的上下文字符串
        """
        results = await self.search(query, top_k=top_k, min_score=0.3)

        if not results:
            return ""

        max_chars = max_tokens * 4
        context_parts = []
        used_chars = 0

        for i, r in enumerate(results):
            header = f"【参考文档 {i + 1}】"
            if r.document.title:
                header += f" {r.document.title}"
            header += f" (相关度: {r.score:.2f})"

            body = r.chunk_text
            available = max_chars - used_chars - len(header) - 10
            if available <= 0:
                break
            if len(body) > available:
                body = body[:available] + "..."

            context_parts.append(f"{header}\n{body}")
            used_chars += len(header) + len(body) + 2

        return "\n\n".join(context_parts)

    def remove_document(self, doc_id: str) -> bool:
        """
        删除文档及其所有分块

        Args:
            doc_id: 文档 ID

        Returns:
            是否成功删除
        """
        if doc_id not in self._documents:
            return False

        # 标记要删除的 chunk 索引
        indices_to_remove = [
            i for i, d in enumerate(self._chunk_to_doc) if d == doc_id
        ]

        if not indices_to_remove:
            del self._documents[doc_id]
            return True

        # 重建索引
        keep_mask = np.ones(len(self._chunks), dtype=bool)
        keep_mask[indices_to_remove] = False

        self._chunks = [c for i, c in enumerate(self._chunks) if keep_mask[i]]
        self._chunk_to_doc = [d for i, d in enumerate(self._chunk_to_doc) if keep_mask[i]]

        if self._embeddings is not None and len(self._embeddings) > 0:
            self._embeddings = self._embeddings[keep_mask]
            if len(self._embeddings) == 0:
                self._embeddings = None

        del self._documents[doc_id]
        logger.info("已删除文档 %s，剩余 %d 个分块", doc_id, len(self._chunks))
        return True

    def clear(self) -> None:
        """清空知识库"""
        self._documents.clear()
        self._chunks.clear()
        self._chunk_to_doc.clear()
        self._embeddings = None
        logger.info("知识库已清空")

    def get_stats(self) -> dict[str, Any]:
        """获取知识库统计信息"""
        return {
            "document_count": len(self._documents),
            "chunk_count": len(self._chunks),
            "total_chars": sum(len(c) for c in self._chunks),
            "embedding_dim": self._embeddings.shape[1] if self._embeddings is not None else 0,
        }

    def list_documents(self) -> list[dict[str, Any]]:
        """列出所有文档"""
        return [
            {
                "id": doc.id,
                "title": doc.title,
                "source": doc.source,
                "content_preview": doc.content[:200] + "..." if len(doc.content) > 200 else doc.content,
                "created_at": doc.created_at,
                "metadata": doc.metadata,
            }
            for doc in self._documents.values()
        ]


# 全局单例
rag_service = RAGService()