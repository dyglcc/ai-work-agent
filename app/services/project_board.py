"""项目管理看板数据服务."""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile


DESKTOP_DIR = Path.home() / "Desktop"
PROJECT_DIR = DESKTOP_DIR / "项目管理"
PROJECT_XLSX = DESKTOP_DIR / "项目管理.xlsx"

_XML_NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
_REL_NS = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


@dataclass
class ProjectRecord:
    source: str
    name: str
    owner: str
    status: str
    progress: str
    level: str
    department: str
    period: str
    goal: str
    latest_update: str
    risks: str
    value: str
    raw: dict[str, str]


def build_project_board() -> dict[str, Any]:
    """读取桌面项目管理数据并生成看板摘要."""
    files = _discover_files()
    projects: list[ProjectRecord] = []
    errors: list[str] = []

    for file_path in files:
        try:
            rows = _read_rows(file_path)
            projects.extend(_rows_to_projects(rows, file_path))
        except Exception as exc:
            errors.append(f"{file_path.name}: {exc}")

    status_counts = Counter(p.status for p in projects)
    owner_counts: dict[str, int] = defaultdict(int)
    for project in projects:
        for owner in _split_owners(project.owner):
            owner_counts[owner] += 1

    departments = Counter(p.department or "未标注" for p in projects)
    levels = Counter(p.level or "未标注" for p in projects)

    total = len(projects)
    completed = status_counts.get("已完成", 0)
    active = status_counts.get("进行中", 0)
    paused = status_counts.get("暂停", 0)
    not_started = status_counts.get("未开始", 0)
    unknown = status_counts.get("未标注", 0)

    return {
        "source_dir": str(PROJECT_DIR),
        "source_files": [str(p) for p in files],
        "updated_at": max((p.stat().st_mtime for p in files), default=0),
        "summary": {
            "total": total,
            "active": active,
            "completed": completed,
            "paused": paused,
            "not_started": not_started,
            "unknown": unknown,
            "completion_rate": round(completed / total * 100, 1) if total else 0,
        },
        "status_counts": dict(status_counts),
        "owner_counts": dict(sorted(owner_counts.items(), key=lambda item: item[1], reverse=True)),
        "department_counts": dict(departments),
        "level_counts": dict(levels),
        "projects": [_project_to_dict(p) for p in projects],
        "insights": _make_insights(projects, status_counts, owner_counts),
        "errors": errors,
    }


def _discover_files() -> list[Path]:
    files: list[Path] = []
    if PROJECT_DIR.exists():
        files.extend(
            p for p in PROJECT_DIR.rglob("*")
            if p.is_file() and p.suffix.lower() in {".xlsx", ".csv", ".tsv"}
        )
    if PROJECT_XLSX.exists():
        files.append(PROJECT_XLSX)
    return sorted(dict.fromkeys(files))


def _read_rows(path: Path) -> list[list[str]]:
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return _read_xlsx_rows(path)
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return [[str(cell or "").strip() for cell in row] for row in csv.reader(f, delimiter=delimiter)]
    return []


def _read_xlsx_rows(path: Path) -> list[list[str]]:
    with ZipFile(path) as zf:
        shared_strings = _read_shared_strings(zf)
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}

        sheet = workbook.find("a:sheets/a:sheet", _XML_NS)
        if sheet is None:
            return []
        rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        target = rel_map[rel_id]
        sheet_path = "xl/" + target.lstrip("/") if not target.startswith("xl/") else target
        root = ET.fromstring(zf.read(sheet_path))

    rows: list[list[str]] = []
    for row in root.findall("a:sheetData/a:row", _XML_NS):
        values: list[str] = []
        for cell in row.findall("a:c", _XML_NS):
            idx = _column_index(cell.attrib.get("r", "A1"))
            while len(values) < idx:
                values.append("")
            values.append(_cell_value(cell, shared_strings))
        while values and values[-1] == "":
            values.pop()
        rows.append(values)
    return rows


def _read_shared_strings(zf: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root.findall("a:si", _XML_NS):
        strings.append("".join(text.text or "" for text in item.findall(".//a:t", _XML_NS)))
    return strings


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    value = cell.find("a:v", _XML_NS)
    if value is None or value.text is None:
        inline = cell.find("a:is", _XML_NS)
        if inline is not None:
            return "".join(text.text or "" for text in inline.findall(".//a:t", _XML_NS)).strip()
        return ""
    text = value.text
    if cell_type == "s":
        return shared_strings[int(text)].strip()
    if cell_type == "b":
        return "是" if text == "1" else "否"
    return text.strip()


def _column_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    idx = 0
    for letter in letters.upper():
        idx = idx * 26 + ord(letter) - 64
    return max(idx - 1, 0)


def _rows_to_projects(rows: list[list[str]], source: Path) -> list[ProjectRecord]:
    header_idx = _find_header_row(rows)
    if header_idx is None:
        return []

    headers = [_normalize_header(cell) for cell in rows[header_idx]]
    records: list[ProjectRecord] = []
    last_record: ProjectRecord | None = None
    for row in rows[header_idx + 1:]:
        if not any(cell.strip() for cell in row):
            continue
        raw = {
            headers[i]: (row[i].strip() if i < len(row) else "")
            for i in range(len(headers))
            if headers[i]
        }
        if not _has_project_identity(raw):
            if last_record and _row_looks_like_continuation(row):
                last_record.latest_update = _first_non_empty(row) or last_record.latest_update
            continue

        record = ProjectRecord(
            source=source.name,
            name=_pick(raw, "项目名称", "项目/任务", "项目", "任务") or "未命名项目",
            owner=_pick(raw, "项目负责人", "负责人", "owner") or "未标注",
            status=_infer_status(raw),
            progress=_infer_progress(raw),
            level=_pick(raw, "项目级别", "级别"),
            department=_pick(raw, "部门/业务条线", "部门", "业务条线"),
            period=_pick(raw, "项目计划周期", "计划周期", "周期", "时间"),
            goal=_pick(raw, "B点（=项目目标）", "B点", "项目目标", "目标"),
            latest_update=_pick(raw, "双周进展", "进展", "最新进展"),
            risks=_pick(raw, "问题反馈", "风险", "问题", "GAP"),
            value=_pick(raw, "为公司带来什么价值", "项目价值", "价值"),
            raw=raw,
        )
        records.append(record)
        last_record = record
    return records


def _find_header_row(rows: list[list[str]]) -> int | None:
    for idx, row in enumerate(rows[:20]):
        joined = " ".join(row)
        if ("项目" in joined or "任务" in joined) and ("负责人" in joined or "部门" in joined):
            return idx
    return 0 if rows else None


def _normalize_header(header: str) -> str:
    return re.sub(r"\s+", "", header.strip())


def _has_project_identity(raw: dict[str, str]) -> bool:
    return bool(_pick(raw, "项目名称", "项目/任务", "项目", "任务") or _pick(raw, "项目负责人", "负责人"))


def _row_looks_like_continuation(row: list[str]) -> bool:
    non_empty = [cell for cell in row if cell.strip()]
    return 0 < len(non_empty) <= 2


def _first_non_empty(row: list[str]) -> str:
    return next((cell.strip() for cell in row if cell.strip()), "")


def _pick(raw: dict[str, str], *names: str) -> str:
    for wanted in names:
        wanted_norm = _normalize_header(wanted).lower()
        for key, value in raw.items():
            key_norm = key.lower()
            if wanted_norm == key_norm or wanted_norm in key_norm or key_norm in wanted_norm:
                if value:
                    return value
    return ""


def _infer_status(raw: dict[str, str]) -> str:
    explicit = _pick(raw, "项目状态", "状态", "进度状态")
    text = " ".join([explicit, _pick(raw, "双周进展", "进展"), _pick(raw, "问题反馈", "风险", "问题")])
    if re.search(r"暂停|搁置|冻结|停滞|hold", text, re.I):
        return "暂停"
    if re.search(r"已完成|完成|结项|上线|交付|done|closed", text, re.I):
        return "已完成"
    if re.search(r"未开始|待启动|未启动|not started", text, re.I):
        return "未开始"
    if explicit:
        return explicit
    return "进行中" if _pick(raw, "项目名称", "项目/任务", "项目") else "未标注"


def _infer_progress(raw: dict[str, str]) -> str:
    explicit = _pick(raw, "项目进度", "进度", "完成率")
    if explicit:
        return explicit
    status = _infer_status(raw)
    if status == "已完成":
        return "100%"
    if status == "未开始":
        return "0%"
    update = _pick(raw, "双周进展", "进展")
    return "有进展记录" if update else "未填写进展"


def _split_owners(owner: str) -> list[str]:
    parts = re.split(r"[、,，/；;]\s*", owner or "")
    return [part.strip() for part in parts if part.strip() and part.strip() != "未标注"]


def _project_to_dict(project: ProjectRecord) -> dict[str, str]:
    return {
        "source": project.source,
        "name": project.name,
        "owner": project.owner,
        "status": project.status,
        "progress": project.progress,
        "level": project.level,
        "department": project.department,
        "period": project.period,
        "goal": _shorten(project.goal, 180),
        "latest_update": _shorten(project.latest_update, 180),
        "risks": _shorten(project.risks, 180),
        "value": _shorten(project.value, 140),
    }


def _make_insights(
    projects: list[ProjectRecord],
    status_counts: Counter[str],
    owner_counts: dict[str, int],
) -> list[str]:
    if not projects:
        return ["未读取到项目数据，请在桌面“项目管理”文件夹或“项目管理.xlsx”中补充项目信息。"]

    insights = [
        f"当前共识别 {len(projects)} 个项目，其中进行中 {status_counts.get('进行中', 0)} 个，已完成 {status_counts.get('已完成', 0)} 个，暂停 {status_counts.get('暂停', 0)} 个。",
    ]
    if owner_counts:
        owner, count = next(iter(sorted(owner_counts.items(), key=lambda item: item[1], reverse=True)))
        insights.append(f"负责人维度中，{owner} 关联项目最多（{count} 个）。")
    missing_updates = [p.name for p in projects if not p.latest_update]
    if missing_updates:
        insights.append(f"{len(missing_updates)} 个项目缺少最新进展，建议补充双周进展用于例会追踪。")
    missing_status = [p.name for p in projects if p.status in {"进行中", "未标注"} and not _pick(p.raw, "项目状态", "状态")]
    if missing_status:
        insights.append("当前表格缺少明确状态列，看板已按默认规则推断状态；建议增加“项目状态/项目进度”列提升准确性。")
    return insights


def _shorten(text: str, max_len: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"
