import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from zipfile import ZipFile

import pytest

from app.config import settings
from app.core.message import Platform, UnifiedMessage
from app.core.skill_loader import (
    InstalledSkillFeature,
    execute_skill,
    install_skill_from_url,
    load_installed_skills,
    set_skill_enabled,
)


class DummyAI:
    async def chat(self, content, system_prompt="", user_id=""):
        return f"{system_prompt.splitlines()[0]}::{content}::{user_id}"


def test_load_skill_from_skill_md(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "skills_dir", str(tmp_path))
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: Demo Skill
description: Demo description
keywords: [demo, 演示]
---

Demo instructions.
""",
        encoding="utf-8",
    )

    skills = load_installed_skills()

    assert len(skills) == 1
    assert skills[0].id == "demo"
    assert skills[0].name == "Demo Skill"
    assert "演示" in skills[0].keywords
    assert skills[0].has_handler is False


@pytest.mark.asyncio
async def test_instruction_skill_executes_with_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "skills_dir", str(tmp_path))
    skill_dir = tmp_path / "writer"
    skill_dir.mkdir()
    (skill_dir / "skill.json").write_text(
        json.dumps({
            "id": "writer",
            "name": "写作助手",
            "keywords": ["写作"],
            "enabled": True,
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text("你是写作助手。", encoding="utf-8")
    message = UnifiedMessage(
        platform=Platform.DINGTALK,
        message_id="m1",
        user_id="u1",
        user_name="u1",
        content="写一段话",
    )

    reply = await execute_skill("writer", message, DummyAI())

    assert reply == "你是写作助手。::写一段话::u1"


@pytest.mark.asyncio
async def test_handler_skill_executes_python_handler(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "skills_dir", str(tmp_path))
    skill_dir = tmp_path / "handler_skill"
    skill_dir.mkdir()
    (skill_dir / "skill.json").write_text(
        json.dumps({
            "id": "handler_skill",
            "name": "Handler Skill",
            "keywords": ["handler"],
            "entry": "handler.py",
        }),
        encoding="utf-8",
    )
    (skill_dir / "handler.py").write_text(
        """async def handle(message, context):
    return f"handled:{message.content}:{context['skill']['id']}"
""",
        encoding="utf-8",
    )
    message = UnifiedMessage(
        platform=Platform.DINGTALK,
        message_id="m1",
        user_id="u1",
        user_name="u1",
        content="hello",
    )

    reply = await execute_skill("handler_skill", message, DummyAI())

    assert reply == "handled:hello:handler_skill"


@pytest.mark.asyncio
async def test_handler_skill_receives_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "skills_dir", str(tmp_path))
    skill_dir = tmp_path / "tool_skill"
    skill_dir.mkdir()
    (skill_dir / "skill.json").write_text(
        json.dumps({
            "id": "tool_skill",
            "name": "Tool Skill",
            "keywords": ["tool"],
            "entry": "handler.py",
        }),
        encoding="utf-8",
    )
    (skill_dir / "handler.py").write_text(
        """async def handle(message, context):
    results = await context["tools"]["web_search"]("example")
    return results[0]["title"]
""",
        encoding="utf-8",
    )
    message = UnifiedMessage(
        platform=Platform.DINGTALK,
        message_id="m1",
        user_id="u1",
        user_name="u1",
        content="hello",
    )

    from app.core import skill_loader

    async def fake_search(query, max_results=5):
        return [{"title": "tool-ok", "url": "https://example.com", "snippet": query}]

    monkeypatch.setitem(skill_loader.tool_registry._tools, "web_search", fake_search)

    reply = await execute_skill("tool_skill", message, DummyAI())

    assert reply == "tool-ok"


def test_toggle_skill_writes_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "skills_dir", str(tmp_path))
    skill_dir = tmp_path / "toggle"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Toggle Skill\nkeywords: [toggle]\n---\nBody",
        encoding="utf-8",
    )

    skill = set_skill_enabled("toggle", False)
    skills = load_installed_skills()

    assert skill.enabled is False
    assert skills[0].enabled is False


def test_installed_skill_feature_matches_name_and_keyword(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "skills_dir", str(tmp_path))
    skill_dir = tmp_path / "match"
    skill_dir.mkdir()
    (skill_dir / "skill.json").write_text(
        json.dumps({
            "id": "match",
            "name": "匹配 Skill",
            "keywords": ["触发词"],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    skill = load_installed_skills()[0]
    feature = InstalledSkillFeature(DummyAI(), skill)

    assert feature.matches("请使用触发词")
    assert feature.matches("请使用匹配 Skill")


def test_infer_codex_style_skill_triggers_from_description(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "skills_dir", str(tmp_path))
    skill_dir = tmp_path / "cny-converter"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        '''---
name: cny-converter
description: Convert a US dollar amount into Chinese yuan (RMB). Make sure to use this skill whenever the user says "算一下" followed by a number, or asks to convert dollars/USD into 人民币/RMB/CNY.
---

当用户说「算一下 + 数字」，或要求把美元换算成人民币时，触发本 skill。
''',
        encoding="utf-8",
    )

    skill = load_installed_skills()[0]
    feature = InstalledSkillFeature(DummyAI(), skill)

    assert "算一下" in skill.keywords
    assert "美元" in skill.keywords
    assert "人民币" in skill.keywords
    assert feature.matches("算一下 250")


@pytest.mark.asyncio
async def test_install_skill_from_url(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "skills_dir", str(tmp_path / "skills"))
    zip_dir = tmp_path / "zips"
    zip_dir.mkdir()
    zip_path = zip_dir / "url-skill.zip"
    with ZipFile(zip_path, "w") as zf:
        zf.writestr("url-skill/skill.json", json.dumps({
            "id": "url_skill",
            "name": "URL Skill",
            "keywords": ["url skill"],
        }))
        zf.writestr("url-skill/SKILL.md", "URL skill body")

    handler = partial(SimpleHTTPRequestHandler, directory=str(zip_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        skill = await install_skill_from_url(f"http://127.0.0.1:{port}/url-skill.zip")
    finally:
        server.shutdown()
        server.server_close()

    assert skill.id == "url_skill"
    assert (tmp_path / "skills" / "url-skill" / "SKILL.md").exists()
