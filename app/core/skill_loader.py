from __future__ import annotations

import importlib.util
import json
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.ai_engine import AIEngine
from app.core.message import UnifiedMessage
from app.core.tools import format_search_results, tool_registry
from app.features.base import Feature

logger = logging.getLogger(__name__)


@dataclass
class SkillDefinition:
    id: str
    name: str
    description: str
    keywords: list[str] = field(default_factory=list)
    enabled: bool = True
    entry: str = ""
    path: Path = field(default_factory=Path)
    instructions: str = ""

    @property
    def has_handler(self) -> bool:
        return bool(self.entry and (self.path / self.entry).is_file())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "keywords": self.keywords,
            "enabled": self.enabled,
            "entry": self.entry,
            "path": str(self.path),
            "has_handler": self.has_handler,
            "has_instructions": bool(self.instructions.strip()),
        }


class InstalledSkillFeature(Feature):
    """把本地 skills 目录中的 Skill 包包装成 Feature."""

    def __init__(self, ai_engine: AIEngine, skill: SkillDefinition) -> None:
        super().__init__(ai_engine)
        self.skill = skill
        self.name = skill.name
        self.keywords = skill.keywords
        self.system_prompt = skill.instructions

    def matches(self, text: str) -> bool:
        if not self.skill.enabled:
            return False
        if self.skill.name and self.skill.name in text:
            return True
        if self.skill.id and self.skill.id in text:
            return True
        return super().matches(text)

    async def handle(self, message: UnifiedMessage) -> str:
        if self.skill.has_handler:
            return await _execute_handler(self.skill, message, self.ai)

        prompt = self.skill.instructions.strip()
        if not prompt:
            prompt = f"你是 Skill：{self.skill.name}。{self.skill.description}"
        tool_context = await _build_tool_context(self.skill, message)
        if tool_context:
            prompt = f"{prompt}\n\n## 可用工具结果\n{tool_context}"
        return await self.ai.chat(message.content, prompt, user_id=message.user_id)


def skills_root() -> Path:
    configured = Path(settings.skills_dir)
    if configured.is_absolute():
        return configured
    return Path.cwd() / configured


def load_installed_skills() -> list[SkillDefinition]:
    root = skills_root()
    root.mkdir(parents=True, exist_ok=True)
    skills: list[SkillDefinition] = []
    for path in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        try:
            skill = _load_skill(path)
        except Exception:
            logger.exception("加载 Skill 失败: %s", path)
            continue
        if skill:
            skills.append(skill)
    return skills


def load_skill_features(ai_engine: AIEngine) -> list[InstalledSkillFeature]:
    return [
        InstalledSkillFeature(ai_engine, skill)
        for skill in load_installed_skills()
        if skill.enabled
    ]


def get_skill(skill_id: str) -> SkillDefinition | None:
    for skill in load_installed_skills():
        if skill.id == skill_id:
            return skill
    return None


def install_skill_zip(zip_path: Path) -> SkillDefinition:
    root = skills_root()
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if not members:
            raise ValueError("Skill 压缩包为空")
        top_names = {Path(m.filename).parts[0] for m in members if Path(m.filename).parts}
        target_name = next(iter(top_names)) if len(top_names) == 1 else zip_path.stem
        target_name = _safe_id(target_name)
        target = root / target_name
        if target.exists():
            raise ValueError(f"Skill 已存在: {target_name}")
        target.mkdir(parents=True)
        try:
            for member in members:
                parts = Path(member.filename).parts
                if not parts:
                    continue
                relative = Path(*parts[1:]) if len(top_names) == 1 else Path(*parts)
                if not relative.parts:
                    continue
                dest = (target / relative).resolve()
                if not _is_relative_to(dest, target.resolve()):
                    raise ValueError(f"压缩包包含非法路径: {member.filename}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
    skill = _load_skill(target)
    if not skill:
        shutil.rmtree(target, ignore_errors=True)
        raise ValueError("压缩包中未找到 skill.json 或 SKILL.md")
    return skill


def set_skill_enabled(skill_id: str, enabled: bool) -> SkillDefinition:
    skill = get_skill(skill_id)
    if not skill:
        raise ValueError(f"Skill 不存在: {skill_id}")
    manifest_path = skill.path / "skill.json"
    manifest = _read_manifest(skill.path)
    if not manifest:
        manifest = {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "keywords": skill.keywords,
            "entry": skill.entry,
        }
    manifest["enabled"] = enabled
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    loaded = _load_skill(skill.path)
    if not loaded:
        raise ValueError(f"Skill 配置无效: {skill_id}")
    return loaded


async def execute_skill(skill_id: str, message: UnifiedMessage, ai_engine: AIEngine) -> str:
    skill = get_skill(skill_id)
    if not skill:
        raise ValueError(f"Skill 不存在: {skill_id}")
    if not skill.enabled:
        raise ValueError(f"Skill 未启用: {skill_id}")
    feature = InstalledSkillFeature(ai_engine, skill)
    return await feature.handle(message)


def _load_skill(path: Path) -> SkillDefinition | None:
    manifest = _read_manifest(path)
    instructions = _read_instructions(path)
    frontmatter = _parse_frontmatter(instructions)

    skill_id = _safe_id(str(manifest.get("id") or frontmatter.get("id") or path.name))
    name = str(manifest.get("name") or frontmatter.get("name") or skill_id)
    description = str(manifest.get("description") or frontmatter.get("description") or "")
    keywords = _list_value(manifest.get("keywords") or frontmatter.get("keywords"))
    keywords.extend(_infer_keywords(name, description, instructions))
    if not keywords:
        keywords = [name, skill_id]
    keywords = list(dict.fromkeys(keyword for keyword in keywords if keyword))
    enabled = bool(manifest.get("enabled", frontmatter.get("enabled", True)))
    entry = str(manifest.get("entry") or frontmatter.get("entry") or "")
    if not entry and (path / "handler.py").is_file():
        entry = "handler.py"

    if not instructions and not entry and not manifest:
        return None
    return SkillDefinition(
        id=skill_id,
        name=name,
        description=description,
        keywords=keywords,
        enabled=enabled,
        entry=entry,
        path=path,
        instructions=instructions,
    )


def _read_manifest(path: Path) -> dict[str, Any]:
    for name in ("skill.json", "manifest.json"):
        file_path = path / name
        if file_path.is_file():
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    return {}


def _read_instructions(path: Path) -> str:
    for name in ("SKILL.md", "skill.md", "CLAUDE.md"):
        file_path = path / name
        if file_path.is_file():
            return file_path.read_text(encoding="utf-8")
    return ""


def _parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}
    data: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key == "keywords":
            data[key] = _list_value(value)
        elif key == "enabled":
            data[key] = value.lower() not in {"false", "0", "no", "off"}
        else:
            data[key] = value
    return data


def _list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        return [part.strip().strip('"').strip("'") for part in re.split(r"[,，]", text) if part.strip()]
    return []


def _infer_keywords(name: str, description: str, instructions: str) -> list[str]:
    text = "\n".join([name or "", description or "", instructions or ""])
    keywords: list[str] = []

    for pattern in (
        r"用户说[「\"]([^」\"]+)[」\"]",
        r"says\s+[\"']([^\"']+)[\"']",
        r"asks?\s+to\s+([^.,;\n]+)",
    ):
        for match in re.finditer(pattern, text, re.I):
            phrase = match.group(1).strip()
            phrase = re.sub(r"\s*\+\s*数字.*$", "", phrase)
            if 1 < len(phrase) <= 32:
                keywords.append(phrase)

    currency_terms = {
        "算一下": ["算一下", "换算", "汇率"],
        "美元": ["美元", "美金", "USD", "$"],
        "人民币": ["人民币", "RMB", "CNY", "¥"],
        "Chinese yuan": ["人民币", "RMB", "CNY"],
        "US dollar": ["美元", "美金", "USD", "$"],
    }
    for marker, terms in currency_terms.items():
        if marker.lower() in text.lower():
            keywords.extend(terms)

    return keywords


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_\-.]+", "_", value).strip(".-")
    return safe or "skill"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


async def _execute_handler(skill: SkillDefinition, message: UnifiedMessage, ai_engine: AIEngine) -> str:
    entry_path = (skill.path / skill.entry).resolve()
    if not _is_relative_to(entry_path, skill.path.resolve()):
        raise ValueError(f"非法 Skill 入口: {skill.entry}")

    module_name = f"ai_work_skill_{skill.id.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, entry_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"无法加载 Skill 入口: {skill.entry}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    handler = getattr(module, "handle", None)
    if handler is None:
        raise ValueError(f"Skill 入口缺少 handle 函数: {skill.entry}")
    result = handler(message, {
        "skill": skill.to_dict(),
        "ai": ai_engine,
        "tools": tool_registry.as_dict(),
    })
    if hasattr(result, "__await__"):
        result = await result
    return str(result)


async def _build_tool_context(skill: SkillDefinition, message: UnifiedMessage) -> str:
    instructions = skill.instructions.lower()
    if "web_search" not in instructions and "web search" not in instructions and "搜索" not in skill.description:
        return ""
    try:
        results = await tool_registry.call("web_search", message.content, max_results=5)
    except Exception as exc:
        logger.warning("Skill %s 调用 web_search 失败: %s", skill.id, exc)
        return f"web_search 调用失败：{exc}"
    return "web_search 结果：\n" + format_search_results(results)
