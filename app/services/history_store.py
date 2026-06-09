from __future__ import annotations

import json
import logging
import os
import pathlib
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# 历史存储目录（可配置）
HISTORY_DIR = pathlib.Path(
    os.environ.get("HISTORY_STORE_DIR", pathlib.Path(__file__).parent.parent.parent / "data" / "history")
)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

# 分类存储文件
CATEGORY_FILES = {
    "project": "project_history.json",
    "chat": "chat_history.json",
    "search": "search_history.json",
    "workflow": "workflow_history.json",
    "rag": "rag_history.json",
}


def _get_file_path(category: str) -> pathlib.Path:
    """获取分类存储文件路径"""
    filename = CATEGORY_FILES.get(category, f"{category}_history.json")
    return HISTORY_DIR / filename


def _read_records(category: str) -> list[dict]:
    """读取指定分类的所有记录"""
    file_path = _get_file_path(category)
    if not file_path.exists():
        return []
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write_records(category: str, records: list[dict]) -> None:
    """写入指定分类的所有记录"""
    file_path = _get_file_path(category)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def save_record(category: str, record: dict, max_records: int = 200) -> None:
    """保存一条历史记录
    
    Args:
        category: 分类（project/chat/search/workflow/rag）
        record: 记录字典，自动添加 timestamp
        max_records: 最大保留记录数
    """
    records = _read_records(category)
    
    # 添加时间戳
    if "timestamp" not in record:
        record["timestamp"] = datetime.now().isoformat()
    if "id" not in record:
        record["id"] = f"{int(time.time() * 1000)}_{len(records)}"
    
    existing_idx = None
    record_id = record.get("id")
    if record_id:
        for idx, existing in enumerate(records):
            if existing.get("id") == record_id:
                existing_idx = idx
                break

    if existing_idx is None:
        records.append(record)
    else:
        records[existing_idx] = {**records[existing_idx], **record}
    
    # 限制最大记录数
    if len(records) > max_records:
        records = records[-max_records:]
    
    _write_records(category, records)
    logger.debug("保存历史记录 [%s]: %s", category, record.get("id", "?"))


def search_records(
    category: str,
    query: str = "",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """搜索历史记录
    
    Args:
        category: 分类（project/chat/search/workflow/rag），为空则搜索全部
        query: 搜索关键词（匹配所有字段）
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        limit: 返回数量限制
        offset: 偏移量
    
    Returns:
        匹配的记录列表
    """
    if category:
        all_records = _read_records(category)
    else:
        # 搜索所有分类
        all_records = []
        for cat in CATEGORY_FILES:
            for r in _read_records(cat):
                r["_category"] = cat
                all_records.append(r)
    
    # 按时间倒序
    all_records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    
    # 过滤
    results = []
    query_lower = query.lower() if query else ""
    
    for record in all_records:
        # 日期过滤
        if start_date or end_date:
            ts = record.get("timestamp", "")
            if ts:
                record_date = ts[:10]  # YYYY-MM-DD
                if start_date and record_date < start_date:
                    continue
                if end_date and record_date > end_date:
                    continue
        
        # 关键词过滤
        if query_lower:
            # 在记录的所有字段中搜索
            record_str = json.dumps(record, ensure_ascii=False).lower()
            if query_lower not in record_str:
                continue
        
        results.append(record)
    
    total = len(results)
    results = results[offset:offset + limit]
    
    return results


def get_recent_records(category: str, limit: int = 20) -> list[dict]:
    """获取最近记录"""
    records = _read_records(category)
    records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return records[:limit]


def delete_record(category: str, record_id: str) -> bool:
    """删除单条记录"""
    records = _read_records(category)
    new_records = [r for r in records if r.get("id") != record_id]
    if len(new_records) == len(records):
        return False
    _write_records(category, new_records)
    return True


def clear_category(category: str) -> None:
    """清空指定分类"""
    _write_records(category, [])


def clear_all() -> None:
    """清空所有历史"""
    for cat in CATEGORY_FILES:
        _write_records(cat, [])


def get_stats() -> dict:
    """获取存储统计"""
    stats = {}
    total = 0
    for cat in CATEGORY_FILES:
        records = _read_records(cat)
        stats[cat] = len(records)
        total += len(records)
    stats["total"] = total
    stats["storage_dir"] = str(HISTORY_DIR)
    return stats
