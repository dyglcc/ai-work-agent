"""项目管理看板数据服务."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile


DESKTOP_DIR = Path.home() / "Desktop"
PROJECT_DIR = DESKTOP_DIR / "项目管理"
PROJECT_XLSX = DESKTOP_DIR / "项目管理.xlsx"
PROJECT_UPDATES_JSON = PROJECT_DIR / "project_updates.json"

_XML_NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
_REL_NS = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


@dataclass
class ProjectRecord:
    source: str
    source_mtime: float
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
    updates = _load_updates()
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

    project_dicts = [_project_to_dict(p, updates) for p in projects]
    update_due_projects = [p for p in project_dicts if p["update_status"]["due"]]
    problem_projects = [p for p in project_dicts if p["problem_status"]["level"] != "green"]

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
            "update_due": len(update_due_projects),
            "new_update_due": sum(1 for p in update_due_projects if p["update_status"]["type"] == "new_project_weekly"),
            "biweekly_update_due": sum(1 for p in update_due_projects if p["update_status"]["type"] in {"no_update", "biweekly_overdue"}),
            "problem": len(problem_projects),
            "high_problem": sum(1 for p in problem_projects if p["problem_status"]["level"] == "red"),
        },
        "status_counts": dict(status_counts),
        "owner_counts": dict(sorted(owner_counts.items(), key=lambda item: item[1], reverse=True)),
        "department_counts": dict(departments),
        "level_counts": dict(levels),
        "projects": project_dicts,
        "update_due_projects": update_due_projects,
        "insights": _make_insights(projects, status_counts, owner_counts),
        "errors": errors,
    }


def add_project_update(project_key: str, content: str, author: str = "") -> dict[str, str]:
    """为项目追加一条人工进展记录."""
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    updates = _load_updates()
    item = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "content": content.strip(),
        "author": author.strip() or "admin",
    }
    updates.setdefault(project_key, []).insert(0, item)
    PROJECT_UPDATES_JSON.write_text(
        json.dumps(updates, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return item


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


def _load_updates() -> dict[str, list[dict[str, str]]]:
    if not PROJECT_UPDATES_JSON.exists():
        return {}
    try:
        data = json.loads(PROJECT_UPDATES_JSON.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                str(key): [item for item in value if isinstance(item, dict)]
                for key, value in data.items()
                if isinstance(value, list)
            }
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


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
    source_mtime = source.stat().st_mtime
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
            source_mtime=source_mtime,
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


def _project_key(project: ProjectRecord) -> str:
    return f"{project.source}::{project.name}::{project.owner}"


def _parse_update_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _week_start(today: date) -> date:
    return today - timedelta(days=today.weekday())


def _update_status(project: ProjectRecord, updates: list[dict[str, str]]) -> dict[str, Any]:
    if project.status == "已完成":
        return {
            "due": False,
            "type": "completed",
            "label": "已完成",
            "reason": "已完成项目不要求更新",
            "last_update_at": "",
            "reminder": "",
            "is_new_project": False,
        }

    now = datetime.now()
    this_week_start = _week_start(now.date())
    source_date = datetime.fromtimestamp(project.source_mtime).date()
    is_new_project = source_date >= this_week_start
    parsed_updates = [
        parsed
        for parsed in (_parse_update_time(str(item.get("timestamp", ""))) for item in updates)
        if parsed is not None
    ]
    last_update = max(parsed_updates) if parsed_updates else None
    updated_this_week = bool(last_update and last_update.date() >= this_week_start)
    owner = project.owner if project.owner and project.owner != "未标注" else "项目负责人"

    if is_new_project and not updated_this_week:
        reason = "新项目需要在当周完成首次进展更新"
        return {
            "due": True,
            "type": "new_project_weekly",
            "label": "新项目本周需更新",
            "reason": reason,
            "last_update_at": last_update.strftime("%Y-%m-%d %H:%M:%S") if last_update else "",
            "reminder": f"请{owner}本周补充《{project.name}》的首次进展、下步计划和当前风险。",
            "is_new_project": True,
        }

    if not last_update:
        reason = "已有项目没有人工双周进展记录"
        return {
            "due": True,
            "type": "no_update",
            "label": "需补双周进展",
            "reason": reason,
            "last_update_at": "",
            "reminder": f"请{owner}补充《{project.name}》本期双周进展、下期计划和风险/阻塞。",
            "is_new_project": is_new_project,
        }

    days_since_update = (now - last_update).days
    if days_since_update >= 14:
        reason = f"距离上次人工更新已 {days_since_update} 天，超过双周更新周期"
        return {
            "due": True,
            "type": "biweekly_overdue",
            "label": "双周更新逾期",
            "reason": reason,
            "last_update_at": last_update.strftime("%Y-%m-%d %H:%M:%S"),
            "reminder": f"请{owner}更新《{project.name}》本期双周进展，并说明风险和下阶段计划。",
            "is_new_project": is_new_project,
        }

    return {
        "due": False,
        "type": "up_to_date",
        "label": "更新正常",
        "reason": f"最近 {days_since_update} 天内已更新",
        "last_update_at": last_update.strftime("%Y-%m-%d %H:%M:%S"),
        "reminder": "",
        "is_new_project": is_new_project,
    }


def _parse_project_dates(project: ProjectRecord) -> tuple[date | None, date | None]:
    text = project.period or " ".join(project.raw.values())
    matches = list(re.finditer(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text))
    dates = [
        date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        for match in matches
    ]
    if len(dates) >= 2:
        return dates[0], dates[1]

    year_match = re.search(r"(20\d{2})年", text)
    if year_match:
        year = int(year_match.group(1))
        month_days = list(re.finditer(r"(\d{1,2})月(\d{1,2})日?", text))
        dates = [
            date(year, int(match.group(1)), int(match.group(2)))
            for match in month_days
        ]
        if len(dates) >= 2:
            return dates[0], dates[1]
    return None, None


def _progress_percent(project: ProjectRecord) -> int | None:
    text = " ".join([project.progress, project.latest_update, project.status])
    percent = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if percent:
        return max(0, min(100, int(float(percent.group(1)))))
    if project.status == "已完成":
        return 100
    if project.status == "未开始":
        return 0
    if project.progress == "未填写进展":
        return None
    return None


def _has_next_action(project: ProjectRecord, updates: list[dict[str, str]]) -> bool:
    text = " ".join([
        project.latest_update,
        project.risks,
        " ".join(str(item.get("content", "")) for item in updates[:3]),
    ])
    if re.search(r"下一步|下步|下阶段|计划|推进|跟进|待办|todo|next|owner|负责人", text, re.I):
        return True
    return False


def _problem_status(
    project: ProjectRecord,
    updates: list[dict[str, str]],
    update_status: dict[str, Any],
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    suggestions: list[str] = []
    today = datetime.now().date()
    _, end_date = _parse_project_dates(project)
    progress = _progress_percent(project)

    if update_status.get("due"):
        issues.append({
            "type": "stale_update",
            "label": "超过更新周期",
            "detail": str(update_status.get("reason", "")),
            "severity": "yellow" if update_status.get("type") != "biweekly_overdue" else "red",
        })
        suggestions.append("先让负责人补一条本期进展，至少包含进展、风险和下步动作。")

    if end_date and end_date <= today and (progress is None or progress < 90) and project.status != "已完成":
        progress_text = "未填写" if progress is None else f"{progress}%"
        issues.append({
            "type": "deadline_low_progress",
            "label": "到期但进度低",
            "detail": f"计划截止 {end_date.isoformat()}，当前进度 {progress_text}",
            "severity": "red",
        })
        suggestions.append("需要确认是否延期、拆分范围，或补充资源推进收尾。")

    if not project.owner or project.owner == "未标注":
        issues.append({
            "type": "missing_owner",
            "label": "负责人不明确",
            "detail": "表格中没有识别到明确负责人",
            "severity": "red",
        })
        suggestions.append("先指定唯一项目 owner，再补充协作人。")

    dependency_text = " ".join([project.latest_update, project.risks, " ".join(project.raw.values())])
    if re.search(r"依赖|待确认|待定|阻塞|卡住|协调|资源不足|缺资源|未解决", dependency_text, re.I):
        issues.append({
            "type": "open_dependency",
            "label": "依赖未解决",
            "detail": "进展或风险中出现依赖/阻塞/协调类信号",
            "severity": "yellow",
        })
        suggestions.append("把依赖方、期望完成时间和需要谁协调写清楚。")

    if not project.goal or project.goal in {"未命名项目", project.name} or len(project.goal) < 8:
        issues.append({
            "type": "unclear_goal",
            "label": "目标不清楚",
            "detail": "项目目标为空或过短，难以判断业务结果",
            "severity": "yellow",
        })
        suggestions.append("补充可验证目标，例如交付物、指标、截止时间或业务结果。")

    if not _has_next_action(project, updates):
        issues.append({
            "type": "missing_next_action",
            "label": "没有下一步动作",
            "detail": "最新进展中未识别到下一步计划或明确动作",
            "severity": "yellow",
        })
        suggestions.append("补充下阶段动作、负责人和完成时间。")

    level = "green"
    if any(issue["severity"] == "red" for issue in issues):
        level = "red"
    elif issues:
        level = "yellow"

    return {
        "level": level,
        "label": "有问题" if level == "red" else "需关注" if level == "yellow" else "正常",
        "issues": issues,
        "suggestions": list(dict.fromkeys(suggestions)),
    }


def _risk_level(project: ProjectRecord, updates: list[dict[str, str]], problem_status: dict[str, Any]) -> str:
    if problem_status["level"] == "red":
        return "red"
    text = " ".join([
        project.status,
        project.progress,
        project.latest_update,
        project.risks,
        " ".join(str(item.get("content", "")) for item in updates),
    ])
    if re.search(r"暂停|搁置|冻结|停滞|延期|延迟|卡住|阻塞|风险|高风险|无法|失败|缺资源|资源不足|hold", text, re.I):
        return "red"
    if re.search(r"待确认|待定|依赖|推进中|协调|关注|缺少|未填写|未明确|问题", text, re.I):
        return "yellow"
    return "green"


def _project_to_dict(project: ProjectRecord, updates: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    key = _project_key(project)
    project_updates = updates.get(key, [])
    update_status = _update_status(project, project_updates)
    problem_status = _problem_status(project, project_updates, update_status)
    return {
        "key": key,
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
        "updates": project_updates,
        "risk_level": _risk_level(project, project_updates, problem_status),
        "update_status": update_status,
        "problem_status": problem_status,
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
