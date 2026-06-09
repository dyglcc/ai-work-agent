from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import pathlib
import re
import tempfile
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import settings
from app.core.ai_engine import AIEngine
from app.core.message import Platform, ReplyMessage, UnifiedMessage
from app.core.router import CommandRouter
from app.core.skill_loader import (
    execute_skill,
    install_skill_zip,
    load_installed_skills,
    set_skill_enabled,
    skills_root,
)
from app.core.workflow import (
    FeatureAgent,
    WorkflowDefinition,
    WorkflowEngine,
    init_workflow_engine,
    get_workflow_engine,
)
from app.platforms.base import PlatformAdapter
from app.services.reminder import reminder_service

# 日志配置
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 全局组件
ai_engine = AIEngine()
cmd_router = CommandRouter(ai_engine)
workflow_engine: WorkflowEngine | None = None
adapters: list[PlatformAdapter] = []

# 消息去重：最近 1000 条 message_id，5 分钟过期
_seen_messages: OrderedDict[str, float] = OrderedDict()
_DEDUP_MAX = 1000
_DEDUP_TTL = 300  # 秒
_seen_messages_lock = asyncio.Lock()

# 临时文件存储
FILE_STORAGE_DIR = pathlib.Path(
    settings.file_storage_dir or pathlib.Path(tempfile.gettempdir()) / "ai-work-agent-files"
)
FILE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
FILE_TTL = 3600  # 文件保留 1 小时


async def _is_duplicate(message_id: str) -> bool:
    if not message_id:
        return False
    now = time.time()
    async with _seen_messages_lock:
        # 清理过期条目
        while _seen_messages:
            oldest_id, ts = next(iter(_seen_messages.items()))
            if now - ts > _DEDUP_TTL:
                _seen_messages.pop(oldest_id)
            else:
                break
        if message_id in _seen_messages:
            return True
        _seen_messages[message_id] = now
        if len(_seen_messages) > _DEDUP_MAX:
            _seen_messages.popitem(last=False)
    return False



def _extract_json(text: str) -> dict | None:
    """从 AI 回复中提取 JSON，支持 ```json 代码块包装."""
    s = text.strip()
    if "```json" in s:
        s = s.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in s:
        s = s.split("```", 1)[1].split("```", 1)[0].strip()
    s = s.strip().strip("`").strip()
    if s.startswith("json"):
        s = s[4:].strip()
    files: list[dict] = []
    images: list[str] = []

    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None

def _cleanup_old_files():
    """清理超过 1 小时的临时文件"""
    now = time.time()
    try:
        for file_path in FILE_STORAGE_DIR.iterdir():
            if file_path.is_file() and (now - file_path.stat().st_mtime) > FILE_TTL:
                file_path.unlink()
                logger.debug("删除过期文件: %s", file_path.name)
    except Exception:
        logger.exception("清理临时文件失败")


def _save_file(content: bytes, extension: str) -> str:
    """保存文件到临时目录，返回文件 ID"""
    _cleanup_old_files()
    file_id = f"{uuid.uuid4().hex}.{extension}"
    file_path = FILE_STORAGE_DIR / file_id
    file_path.write_bytes(content)
    return file_id


def _file_url(file_id: str) -> str:
    base_url = settings.public_base_url.rstrip("/") if settings.public_base_url else ""
    path = f"/files/{file_id}"
    return f"{base_url}{path}" if base_url else path


def _repair_json_text(text: str) -> str:
    repaired = text.strip()
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    repaired = re.sub(
        r'("content"\s*:\s*\[[^\[\]{}]*?)\s*}\s*,?\s*\]\s*,\s*{',
        r"\1]\n    },\n    {",
        repaired,
        flags=re.DOTALL,
    )
    repaired = re.sub(
        r'("content"\s*:\s*\[[^\[\]{}]*?)\s*}(\s*,?\s*(?:\n\s*)?[\]}])',
        r"\1]}\2",
        repaired,
        flags=re.DOTALL,
    )
    return repaired


def _extract_json(text: str) -> dict | None:
    s = text.strip()
    if "```json" in s:
        s = s.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in s:
        s = s.split("```", 1)[1].split("```", 1)[0].strip()
    s = s.strip().strip("`").strip()
    if s.startswith("json"):
        s = s[4:].strip()

    candidates = [s]
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        candidates.append(s[start : end + 1])

    for candidate in candidates:
        for value in (candidate, _repair_json_text(candidate)):
            try:
                data = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    return None


def _is_ppt_payload(data: dict) -> bool:
    return isinstance(data.get("title"), str) and isinstance(data.get("slides"), list)


async def _materialize_generated_files(reply_text: str) -> tuple[str, list[dict], list[str]]:
    """Convert feature JSON markers into generated files usable by Web and platform adapters."""
    from app.services.file_gen import generate_pptx, generate_docx, generate_chart, generate_image

    files: list[dict] = []
    images: list[str] = []

    data: dict | None = None
    if reply_text.startswith("__PPT_JSON__\n"):
        data = _extract_json(reply_text.split("__PPT_JSON__\n", 1)[1])
    else:
        parsed = _extract_json(reply_text)
        if parsed and _is_ppt_payload(parsed):
            data = parsed

    if data and _is_ppt_payload(data):
        pptx_content = generate_pptx(data["title"], data["slides"])
        file_id = _save_file(pptx_content, "pptx")
        files.append({
            "name": f"{data['title']}.pptx",
            "url": _file_url(file_id),
            "type": "pptx",
            "path": str(FILE_STORAGE_DIR / file_id),
        })
        return data.get("summary", f"已生成 PPT：{data['title']}"), files, images

    if reply_text.startswith("__REPORT_JSON__\n"):
        data = json.loads(reply_text.split("__REPORT_JSON__\n", 1)[1])
        docx_content = generate_docx(data["title"], data["sections"])
        file_id = _save_file(docx_content, "docx")
        files.append({
            "name": f"{data['title']}.docx",
            "url": _file_url(file_id),
            "type": "docx",
            "path": str(FILE_STORAGE_DIR / file_id),
        })
        return data.get("summary", f"已生成报告：{data['title']}"), files, images

    if reply_text.startswith("__CHART_JSON__\n"):
        data = json.loads(reply_text.split("__CHART_JSON__\n", 1)[1])
        chart_content = generate_chart(data["chart_type"], data["data"], data["title"])
        file_id = _save_file(chart_content, "png")
        images.append(_file_url(file_id))
        return data.get("summary", f"已生成图表：{data['title']}"), files, images

    if reply_text.startswith("__IMAGE_JSON__\n"):
        data = _extract_json(reply_text.split("__IMAGE_JSON__\n", 1)[1])
        if data is None:
            raise ValueError("无法解析 AI 返回的图片 JSON 数据")
        image_content = await generate_image(
            data["prompt"],
            title=data.get("title", "AI 作图"),
            subtitle=data.get("subtitle", ""),
            body=data.get("body", ""),
            style=data.get("style", ""),
            palette=data.get("palette", "blue"),
            elements=data.get("elements", []),
        )
        file_id = _save_file(image_content, "png")
        images.append(_file_url(file_id))
        return data.get("summary", "已生成图片"), files, images

    return reply_text, files, images


def _append_platform_attachments(reply_text: str, files: list[dict], images: list[str]) -> str:
    parts = [reply_text]
    if files:
        parts.append("")
        parts.append("生成的文件：")
        for file in files:
            parts.append(f"- {file['name']}: {file['url']}")
    if images:
        parts.append("")
        parts.append("生成的图片：")
        for url in images:
            parts.append(f"- {url}")
    return "\n".join(parts)


def _match_feature_name(content: str) -> str:
    from app.core.router import _CLEAR_KEYWORDS

    stripped = content.strip()
    if stripped in _CLEAR_KEYWORDS:
        return "系统"
    for feature in cmd_router.features:
        if feature.matches(stripped):
            return feature.name
    return cmd_router.fallback.name


def _save_chat_history(
    *,
    platform: str,
    message_id: str,
    user_id: str,
    user_name: str,
    user_message: str,
    assistant_reply: str,
    feature: str,
    is_group: bool = False,
    group_id: str = "",
    files: list[dict] | None = None,
    images: list[str] | None = None,
    status: str = "ok",
    record_id: str = "",
) -> None:
    """Persist one completed chat turn for admin review."""
    from app.services.history_store import save_record

    conversation_id = group_id if is_group and group_id else user_id
    record = {
        "platform": platform,
        "message_id": message_id,
        "conversation_id": conversation_id,
        "user_id": user_id,
        "user_name": user_name,
        "is_group": is_group,
        "group_id": group_id,
        "feature": feature,
        "user_message": user_message,
        "assistant_reply": assistant_reply,
        "files": files or [],
        "images": images or [],
        "status": status,
    }
    if record_id:
        record["id"] = record_id

    try:
        save_record(
            "chat",
            record,
            max_records=2000,
        )
    except Exception:
        logger.exception("保存聊天历史失败")


def _chat_history_id(platform: str, message_id: str) -> str:
    return f"{platform}:{message_id}" if message_id else f"{platform}:{uuid.uuid4().hex}"


async def handle_message(message: UnifiedMessage) -> None:
    """统一消息处理入口：去重 → 路由 → 回复."""
    if await _is_duplicate(message.message_id):
        logger.debug("重复消息，跳过: %s", message.message_id)
        return

    logger.info(
        "收到消息 [%s] from %s: %s",
        message.platform.value,
        message.user_name,
        message.content[:50],
    )

    files: list[dict] = []
    images: list[str] = []
    feature_name = _match_feature_name(message.content)
    status = "ok"
    history_id = _chat_history_id(message.platform.value, message.message_id)
    _save_chat_history(
        platform=message.platform.value,
        message_id=message.message_id,
        user_id=message.user_id,
        user_name=message.user_name,
        user_message=message.content,
        assistant_reply="",
        feature=feature_name,
        is_group=message.is_group,
        group_id=message.group_id,
        status="processing",
        record_id=history_id,
    )
    try:
        reply_text = await cmd_router.route(message)
        reply_text, files, images = await _materialize_generated_files(reply_text)
        if files or images:
            logger.info("已生成附件: files=%s images=%s", files, images)
        reply_text = _append_platform_attachments(reply_text, files, images)
    except Exception:
        logger.exception("处理消息时出错")
        reply_text = "抱歉，处理您的消息时出现了错误，请稍后重试。"
        status = "error"

    try:
        _save_chat_history(
            platform=message.platform.value,
            message_id=message.message_id,
            user_id=message.user_id,
            user_name=message.user_name,
            user_message=message.content,
            assistant_reply=reply_text,
            feature=feature_name,
            is_group=message.is_group,
            group_id=message.group_id,
            files=files,
            images=images,
            status=status,
            record_id=history_id,
        )
    except Exception:
        logger.exception("保存聊天历史失败")

    reply = ReplyMessage(
        content=reply_text,
        source_message=message,
        files=files,
        images=images,
    )

    # 找到对应平台的适配器发送回复
    for adapter in adapters:
        if adapter.platform != message.platform:
            continue
        try:
            await adapter.send_reply(reply)
        except Exception:
            logger.exception("发送回复失败 [%s]", adapter.platform.value)
        break
    else:
        logger.warning("未找到平台 %s 的适配器", message.platform.value)


def _setup_workflow_engine() -> WorkflowEngine:
    """初始化 A2A 工作流引擎，注册所有 Feature Agent."""
    engine = init_workflow_engine(ai_engine)

    from app.features.report import ReportFeature
    from app.features.meeting import MeetingFeature
    from app.features.translate import TranslateFeature
    from app.features.code import CodeFeature
    from app.features.chart import ChartFeature
    from app.features.email import EmailFeature
    from app.features.reminder import ReminderFeature
    from app.features.ppt import PPTFeature
    from app.features.image_gen import ImageGenFeature
    from app.features.summary import SummaryFeature
    from app.features.project import ProjectManagementFeature

    feature_specs = [
        ("report", "日报周报", ReportFeature, "生成日报/周报/月报等总结报告"),
        ("meeting", "会议纪要", MeetingFeature, "整理会议内容，生成会议纪要"),
        ("translate", "翻译", TranslateFeature, "多语言翻译"),
        ("code", "代码助手", CodeFeature, "代码生成、解释、优化"),
        ("chart", "图表生成", ChartFeature, "数据可视化图表生成"),
        ("email", "邮件编辑", EmailFeature, "邮件起草和编辑"),
        ("reminder", "智能提醒", ReminderFeature, "定时提醒任务"),
        ("ppt", "PPT生成", PPTFeature, "演示文稿生成"),
        ("image_gen", "图片生成", ImageGenFeature, "AI 图片生成"),
        ("summary", "内容总结", SummaryFeature, "文本内容总结"),
        ("project", "项目管理", ProjectManagementFeature, "WBS 拆解与排期偏差预警"),
    ]

    for agent_id, name, feature_cls, description in feature_specs:
        feature = feature_cls(ai_engine)

        def make_handler(feat, aid):
            async def handler(task_id: str, instruction: str) -> str:
                msg = UnifiedMessage(
                    platform=Platform.DINGTALK,
                    message_id=task_id,
                    user_id=aid,
                    user_name=aid,
                    content=instruction,
                )
                return await feat.handle(msg)
            return handler

        agent = FeatureAgent(agent_id, name, description, make_handler(feature, agent_id))
        engine.register_agent(agent)

    for skill in load_installed_skills():
        if not skill.enabled:
            continue

        async def skill_handler(task_id: str, instruction: str, skill_id: str = skill.id) -> str:
            msg = UnifiedMessage(
                platform=Platform.DINGTALK,
                message_id=task_id,
                user_id=skill_id,
                user_name=skill_id,
                content=instruction,
            )
            return await execute_skill(skill_id, msg, ai_engine)

        agent = FeatureAgent(
            f"skill:{skill.id}",
            skill.name,
            skill.description or "本地安装 Skill",
            skill_handler,
        )
        for keyword in skill.keywords:
            agent.add_capability(keyword, skill.description or skill.name, [keyword])
        engine.register_agent(agent)

    # 预定义工作流 1: 日报生成（总结 → 报告）
    daily_report = WorkflowDefinition("daily_report", "日报生成：先总结内容要点，再输出日报")
    daily_report.add_step("summary", "请总结以下内容的要点：{context}")
    daily_report.add_step("report", "基于以下要点生成一份日报：{context}")
    engine.register_workflow(daily_report)

    # 预定义工作流 2: 会议全流程（纪要 → 总结 → 报告）
    meeting_full = WorkflowDefinition("meeting_full", "会议全流程：纪要 → 总结 → 报告")
    meeting_full.add_step("meeting", "请整理以下会议内容，生成会议纪要：{context}")
    meeting_full.add_step("summary", "请总结以下会议纪要的关键要点：{context}")
    meeting_full.add_step("report", "基于以下会议要点生成一份会议报告：{context}")
    engine.register_workflow(meeting_full)

    # 预定义工作流 3: 内容创作（翻译 → 总结）
    content = WorkflowDefinition("content_creation", "内容创作：翻译 → 总结")
    content.add_step("translate", "请将以下内容翻译成英文：{context}")
    content.add_step("summary", "请总结以下翻译结果的要点：{context}")
    engine.register_workflow(content)

    logger.info("A2A 工作流引擎已初始化：%d 个 Agent，%d 个工作流",
                len(engine.list_agents()), len(engine.list_workflows()))
    return engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动/关闭平台连接."""
    global workflow_engine

    # 启动工作流引擎
    workflow_engine = _setup_workflow_engine()

    # 启动
    if settings.dingtalk_enabled:
        try:
            from app.platforms.dingtalk.adapter import DingTalkAdapter

            dt = DingTalkAdapter()
            dt.set_message_handler(handle_message)
            await dt.start()
            adapters.append(dt)
            logger.info("钉钉适配器已启动")
        except Exception:
            logger.exception("钉钉适配器启动失败")

    if settings.feishu_enabled:
        try:
            from app.platforms.feishu.adapter import FeishuAdapter

            fs = FeishuAdapter()
            fs.set_message_handler(handle_message)
            await fs.start()
            adapters.append(fs)
            logger.info("飞书适配器已启动")
        except Exception:
            logger.exception("飞书适配器启动失败")

    if not adapters:
        logger.warning("没有启用任何平台适配器，请检查配置")

    # 启动提醒服务
    reminder_service.start()

    yield

    # 关闭提醒服务
    reminder_service.stop()

    # 关闭
    for adapter in adapters:
        try:
            await adapter.stop()
        except Exception:
            logger.exception("关闭适配器失败")
    adapters.clear()


app = FastAPI(title="AI 工作助手", lifespan=lifespan)

# 挂载静态文件目录
static_dir = pathlib.Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# CORS 支持（允许 Web 页面调用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- HTTP 聊天接口 ----

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户消息")
    user_id: str = Field(default="", description="用户ID，用于多轮对话记忆")


class RecallRequest(BaseModel):
    user_id: str = Field(default="", description="用户ID，用于撤回上一轮对话")


class FileInfo(BaseModel):
    name: str
    url: str
    type: str


class ChatResponse(BaseModel):
    reply: str
    user_id: str
    feature: str  # 命中的功能模块名
    files: list[FileInfo] = []  # 可下载的文件列表
    images: list[str] = []  # 图片 URL 列表


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """HTTP 聊天接口：直接发消息，返回 AI 回复."""
    from app.services.file_gen import generate_pptx, generate_docx, generate_chart, generate_image

    user_id = req.user_id or f"web_{uuid.uuid4().hex[:8]}"

    # 构造统一消息
    message = UnifiedMessage(
        platform=Platform.DINGTALK,  # HTTP 接口复用 platform 字段不影响逻辑
        message_id=uuid.uuid4().hex,
        user_id=user_id,
        user_name=user_id,
        content=req.message,
    )

    # 判断路由到哪个功能
    feature_name = "通用助手"
    content = req.message.strip()

    # 检查清除指令
    from app.core.router import _CLEAR_KEYWORDS
    if content in _CLEAR_KEYWORDS:
        ai_engine.memory.clear(user_id)
        reply_text = "会话已清空，我们可以重新开始对话了。"
        _save_chat_history(
            platform="web",
            message_id=message.message_id,
            user_id=user_id,
            user_name=user_id,
            user_message=req.message,
            assistant_reply=reply_text,
            feature="系统",
        )
        return ChatResponse(reply=reply_text, user_id=user_id, feature="系统")

    for feature in cmd_router.features:
        if feature.matches(content):
            feature_name = feature.name
            reply_text = await feature.handle(message)
            break
    else:
        feature_name = cmd_router.fallback.name
        reply_text = await cmd_router.fallback.handle(message)

    # 处理特殊标记的 JSON 响应
    files = []
    images = []

    try:
        # PPT 生成
        if reply_text.startswith("__PPT_JSON__\n"):
            json_str = reply_text.split("__PPT_JSON__\n", 1)[1]
            data = json.loads(json_str)

            # 生成 PPT 文件
            pptx_content = generate_pptx(data["title"], data["slides"])
            file_id = _save_file(pptx_content, "pptx")

            files.append(FileInfo(
                name=f"{data['title']}.pptx",
                url=f"/files/{file_id}",
                type="pptx"
            ))
            reply_text = data.get("summary", f"已生成 PPT：{data['title']}")

        # 报告生成
        elif reply_text.startswith("__REPORT_JSON__\n"):
            json_str = reply_text.split("__REPORT_JSON__\n", 1)[1]
            data = json.loads(json_str)

            # 生成 Word 文件
            docx_content = generate_docx(data["title"], data["sections"])
            file_id = _save_file(docx_content, "docx")

            files.append(FileInfo(
                name=f"{data['title']}.docx",
                url=f"/files/{file_id}",
                type="docx"
            ))
            reply_text = data.get("summary", f"已生成报告：{data['title']}")

        # 图表生成
        elif reply_text.startswith("__CHART_JSON__\n"):
            json_str = reply_text.split("__CHART_JSON__\n", 1)[1]
            data = json.loads(json_str)

            # 生成图表
            chart_content = generate_chart(
                data["chart_type"],
                data["data"],
                data["title"]
            )
            file_id = _save_file(chart_content, "png")

            images.append(f"/files/{file_id}")
            reply_text = data.get("summary", f"已生成图表：{data['title']}")

        # 图片生成
        elif reply_text.startswith("__IMAGE_JSON__\n"):
            json_str = reply_text.split("__IMAGE_JSON__\n", 1)[1]
            data = json.loads(json_str)

            # 调用图片生成 API
            image_content = await generate_image(
                data["prompt"],
                title=data.get("title", "AI 作图"),
                subtitle=data.get("subtitle", ""),
                body=data.get("body", ""),
                style=data.get("style", ""),
                palette=data.get("palette", "blue"),
                elements=data.get("elements", []),
            )
            file_id = _save_file(image_content, "png")

            images.append(f"/files/{file_id}")
            reply_text = data.get("summary", "已生成图片")

        # 提醒功能
        elif reply_text.startswith("__REMINDER_JSON__\n"):
            json_str = reply_text.split("__REMINDER_JSON__\n", 1)[1]
            data = _extract_json(json_str)
            if data is None:
                raise ValueError("无法解析 AI 返回的提醒 JSON 数据")

            minutes = float(data["minutes"])
            content = data["content"]
            reminder_service.add_reminder(user_id, minutes, content)
            reply_text = data.get("summary", f"好的，{int(minutes)}分钟后提醒你{content}")

        # 会议纪要
        elif reply_text.startswith("__MEETING_JSON__\n"):
            json_str = reply_text.split("__MEETING_JSON__\n", 1)[1]
            data = _extract_json(json_str)
            if data is None:
                raise ValueError("无法解析 AI 返回的会议纪要 JSON 数据")

            # 生成会议纪要 Word 文件
            title = data.get("title", "会议纪要")
            sections = []
            if data.get("summary"):
                sections.append({"heading": "会议摘要", "paragraphs": [data["summary"]]})
            if data.get("key_points"):
                sections.append({"heading": "关键要点", "paragraphs": [f"• {p}" for p in data["key_points"]]})
            if data.get("action_items"):
                items_list = [
                    f"• {item['task']}（负责人: {item.get('owner', '待定')}, 截止: {item.get('deadline', '待定')}）"
                    for item in data["action_items"]
                ]
                sections.append({"heading": "行动项", "paragraphs": items_list})
            if data.get("suggestions"):
                sections.append({"heading": "建议", "paragraphs": [f"• {s}" for s in data["suggestions"]]})

            docx_content = generate_docx(title, sections)
            file_id = _save_file(docx_content, "docx")
            files.append(FileInfo(name=f"{title}.docx", url=f"/files/{file_id}", type="docx"))

            # 构建可读的回复文本
            reply_parts = [f"📋 {title}", ""]
            if data.get("summary"):
                reply_parts.append(f"📝 摘要：{data['summary']}")
                reply_parts.append("")
            if data.get("key_points"):
                reply_parts.append("🔑 关键要点：")
                for p in data["key_points"]:
                    reply_parts.append(f"  • {p}")
                reply_parts.append("")
            if data.get("action_items"):
                reply_parts.append("✅ 行动项：")
                for item in data["action_items"]:
                    reply_parts.append(f"  • {item['task']}（{item.get('owner', '待定')} / {item.get('deadline', '待定')}）")
                reply_parts.append("")
            if data.get("suggestions"):
                reply_parts.append("💡 建议：")
                for s in data["suggestions"]:
                    reply_parts.append(f"  • {s}")

            reply_text = "\n".join(reply_parts)

    except Exception as e:
        logger.exception("处理文件生成时出错")
        reply_text += f"\n\n（文件生成失败：{str(e)}）"

    response_files = [file.model_dump() for file in files]
    _save_chat_history(
        platform="web",
        message_id=message.message_id,
        user_id=user_id,
        user_name=user_id,
        user_message=req.message,
        assistant_reply=reply_text,
        feature=feature_name,
        files=response_files,
        images=images,
    )

    return ChatResponse(
        reply=reply_text,
        user_id=user_id,
        feature=feature_name,
        files=files,
        images=images
    )


@app.post("/recall")
async def recall_message(req: RecallRequest):
    """撤回上一轮对话"""
    user_id = req.user_id or f"web_{uuid.uuid4().hex[:8]}"

    success = ai_engine.memory.recall_last(user_id)

    if success:
        return {"success": True, "message": "已撤回上一轮对话"}
    else:
        return {"success": False, "message": "没有可撤回的对话"}


@app.get("/reminders")
async def get_reminders(user_id: str = ""):
    """获取用户待执行的提醒列表"""
    if not user_id:
        return {"reminders": []}

    pending = reminder_service.get_pending(user_id)
    return {
        "reminders": [
            {
                "id": r.id,
                "content": r.content,
                "trigger_at": r.trigger_at,
                "created_at": r.created_at,
                "remaining_seconds": max(0, r.trigger_at - time.time()),
            }
            for r in pending
        ]
    }


@app.get("/reminders/triggered")
async def get_triggered_reminders(user_id: str = ""):
    """获取已触发但未读的提醒"""
    if not user_id:
        return {"reminders": []}

    triggered = reminder_service.get_triggered(user_id)
    # 自动标记为已读
    if triggered:
        reminder_service.mark_read([r.id for r in triggered])

    return {
        "reminders": [
            {
                "id": r.id,
                "content": r.content,
                "trigger_at": r.trigger_at,
            }
            for r in triggered
        ]
    }


@app.post("/upload")
async def upload_file(file: UploadFile = File(...), user_id: str = ""):
    """上传附件，提取文本内容（如果可能）供 AI 分析"""
    content = await file.read()
    filename = file.filename or "unnamed"
    file_type = filename.rsplit(".", 1)[-1].lower() if "." in filename else "unknown"

    # 保存文件
    file_id = _save_file(content, file_type)

    # 尝试提取文本内容
    text_content = ""
    try:
        if file_type == "txt" or file_type == "csv":
            text_content = content.decode("utf-8", errors="ignore")[:5000]
        elif file_type == "docx":
            from docx import Document as DocxDocument
            import io
            doc = DocxDocument(io.BytesIO(content))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            text_content = "\n".join(paragraphs)[:5000]
        elif file_type in ("png", "jpg", "jpeg", "gif", "webp"):
            text_content = f"[用户上传了一张图片: {filename}]"
        elif file_type == "pdf":
            text_content = f"[用户上传了 PDF 文件: {filename}]"
        elif file_type in ("xls", "xlsx"):
            text_content = f"[用户上传了 Excel 文件: {filename}]"
        elif file_type in ("ppt", "pptx"):
            text_content = f"[用户上传了 PPT 文件: {filename}]"
        else:
            text_content = f"[用户上传了文件: {filename}]"
    except Exception as e:
        logger.warning("提取文件内容失败: %s", e)
        text_content = f"[用户上传了文件: {filename}，无法读取内容]"

    return {
        "success": True,
        "file_id": file_id,
        "filename": filename,
        "type": file_type,
        "url": f"/files/{file_id}",
        "text_content": text_content,
    }


# ---- 语音转写接口 ----

@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    """接收音频文件，调用 Whisper API 转写为文字."""
    import httpx

    whisper_url = settings.whisper_api_url
    whisper_key = settings.whisper_api_key or settings.anthropic_api_key
    whisper_model = settings.whisper_model

    if not whisper_url:
        # 根据 anthropic_base_url 自动推断
        base = settings.anthropic_base_url.rstrip("/")
        # 去掉末尾的 /anthropic 等路径，拼 OpenAI 兼容路径
        if "/anthropic" in base:
            base = base.split("/anthropic")[0]
        whisper_url = f"{base}/v1/audio/transcriptions"

    audio_data = await file.read()
    filename = file.filename or "audio.webm"

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                whisper_url,
                headers={"Authorization": f"Bearer {whisper_key}"},
                files={"file": (filename, audio_data, file.content_type or "audio/webm")},
                data={"model": whisper_model},
            )
        if resp.status_code == 200:
            result = resp.json()
            text = result.get("text", "")
            return {"text": text}
        else:
            logger.error("Whisper API 错误: %s %s", resp.status_code, resp.text)
            return {"text": "", "error": f"转写失败 ({resp.status_code})"}
    except Exception as e:
        logger.exception("调用 Whisper API 失败")
        return {"text": "", "error": str(e)}


# ---- 文档解析 API 端点 ----
from app.services.doc_parser import parse_document, is_supported, get_supported_types


class DocParseRequest(BaseModel):
    user_id: str = Field(default="", description="用户ID")


@app.get("/parse/supported")
async def supported_types():
    """返回支持的文档类型列表"""
    return {"types": get_supported_types()}


@app.post("/parse")
async def parse_file(file: UploadFile = File(...), user_id: str = ""):
    """解析上传的文档，提取文本内容"""
    if not is_supported(file.filename or ""):
        return {
            "success": False,
            "filename": file.filename,
            "error": f"不支持的文件类型: {pathlib.Path(file.filename or '').suffix}",
            "supported_types": get_supported_types(),
        }

    content = await file.read()
    result = parse_document(content, file.filename or "unknown")

    return result


@app.post("/parse/and/index")
async def parse_and_index(file: UploadFile = File(...), user_id: str = ""):
    """解析文档并自动添加到知识库索引"""
    if not is_supported(file.filename or ""):
        return {
            "success": False,
            "filename": file.filename,
            "error": f"不支持的文件类型: {pathlib.Path(file.filename or '').suffix}",
            "supported_types": get_supported_types(),
        }

    content = await file.read()
    parse_result = parse_document(content, file.filename or "unknown")

    if not parse_result["success"]:
        return parse_result

    # 自动添加到知识库
    try:
        from app.services.rag import rag_service
        doc_id = await rag_service.add_document(
            content=parse_result["text"],
            title=file.filename or "未命名文档",
            source=f"upload:{file.filename}",
            metadata=parse_result["metadata"],
        )
        return {
            **parse_result,
            "indexed": True,
            "document_id": doc_id,
        }
    except Exception as e:
        logger.exception("索引文档失败")
        return {
            **parse_result,
            "indexed": False,
            "index_error": str(e),
        }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "platforms": [type(a).__name__ for a in adapters],
        "ai_provider": settings.ai_provider,
        "ai_model": ai_engine.model,
        "workflow_agents": len(workflow_engine.list_agents()) if workflow_engine else 0,
        "workflow_workflows": len(workflow_engine.list_workflows()) if workflow_engine else 0,
    }


@app.get("/files/{file_id}")
async def download_file(file_id: str):
    """下载生成的文件"""
    file_path = FILE_STORAGE_DIR / file_id

    if not file_path.exists() or not file_path.is_file():
        return Response(content="文件不存在或已过期", status_code=404)

    # 根据扩展名确定 MIME 类型和下载文件名
    extension = file_path.suffix.lower()
    mime_types = {
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".png": "image/png",
    }

    media_type = mime_types.get(extension, "application/octet-stream")

    # 生成友好的文件名
    if extension == ".pptx":
        filename = "演示文稿.pptx"
    elif extension == ".docx":
        filename = "报告文档.docx"
    elif extension == ".png":
        filename = "图片.png"
    else:
        filename = file_id

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=filename
    )


# ---- A2A 工作流 API 端点 ----

class WorkflowExecuteRequest(BaseModel):
    workflow_name: str = Field(..., description="工作流名称")
    message: str = Field(..., min_length=1, description="初始消息/输入内容")


class WorkflowDynamicRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户需求描述")
    user_id: str = Field(default="", description="用户ID")


@app.get("/workflow/agents")
async def list_agents():
    """列出所有注册的 Agent"""
    if not workflow_engine:
        return {"agents": []}
    agents = workflow_engine.list_agents()
    return {
        "agents": [
            {
                "id": a.id,
                "name": a.name,
                "description": a.description,
                "status": a.status.value,
                "capabilities": [{"name": c.name, "description": c.description} for c in a.capabilities],
            }
            for a in agents
        ]
    }


@app.get("/workflow/workflows")
async def list_workflows():
    """列出所有注册的工作流"""
    if not workflow_engine:
        return {"workflows": []}
    return {"workflows": workflow_engine.list_workflows()}


@app.post("/workflow/execute")
async def execute_workflow(req: WorkflowExecuteRequest):
    """执行预定义工作流"""
    if not workflow_engine:
        return {"success": False, "error": "工作流引擎未初始化"}

    try:
        results = await workflow_engine.execute_workflow(req.workflow_name, req.message)
        return {
            "success": True,
            "workflow": req.workflow_name,
            "steps": [
                {
                    "task_id": r.task_id,
                    "agent_id": r.agent_id,
                    "success": r.success,
                    "result": r.result,
                    "error": r.error,
                }
                for r in results
            ],
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.exception("执行工作流失败")
        return {"success": False, "error": str(e)}


@app.post("/workflow/dynamic")
async def execute_dynamic_workflow(req: WorkflowDynamicRequest):
    """动态编排工作流：AI 自动分析需求并编排 Agent 执行"""
    if not workflow_engine:
        return {"success": False, "error": "工作流引擎未初始化"}

    try:
        user_id = req.user_id or f"wf_{uuid.uuid4().hex[:8]}"
        results = await workflow_engine.execute_dynamic(req.message, user_id=user_id)
        return {
            "success": True,
            "steps": [
                {
                    "task_id": r.task_id,
                    "agent_id": r.agent_id,
                    "success": r.success,
                    "result": r.result,
                    "error": r.error,
                }
                for r in results
            ],
        }
    except Exception as e:
        logger.exception("动态工作流执行失败")
        return {"success": False, "error": str(e)}


# ---- RAG 知识库 API 端点 ----
from app.services.rag import rag_service


class RAGDocumentRequest(BaseModel):
    content: str = Field(..., min_length=1, description="文档内容")
    title: str = Field(default="", description="文档标题")
    source: str = Field(default="", description="来源标识")
    metadata: dict = Field(default_factory=dict, description="附加元数据")


class RAGSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="查询文本")
    top_k: int = Field(default=5, ge=1, le=20, description="返回结果数量")


class RAGChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户消息")
    user_id: str = Field(default="", description="用户ID")
    use_rag: bool = Field(default=True, description="是否启用 RAG 知识库增强")


@app.post("/rag/documents")
async def add_document(req: RAGDocumentRequest):
    """添加文档到知识库"""
    try:
        doc_id = await rag_service.add_document(
            content=req.content,
            title=req.title,
            source=req.source,
            metadata=req.metadata,
        )
        return {"success": True, "document_id": doc_id}
    except Exception as e:
        logger.exception("添加文档失败")
        return {"success": False, "error": str(e)}


@app.get("/rag/documents")
async def list_documents():
    """列出知识库中的所有文档"""
    return {"documents": rag_service.list_documents()}


@app.get("/rag/stats")
async def rag_stats():
    """获取知识库统计信息"""
    return rag_service.get_stats()


@app.post("/rag/search")
async def search_rag(req: RAGSearchRequest):
    """检索知识库"""
    try:
        results = await rag_service.search(req.query, top_k=req.top_k)
        return {
            "query": req.query,
            "results": [
                {
                    "document_id": r.document.id,
                    "title": r.document.title,
                    "source": r.document.source,
                    "score": round(r.score, 4),
                    "chunk_index": r.chunk_index,
                    "chunk_text": r.chunk_text[:500],
                }
                for r in results
            ],
        }
    except Exception as e:
        logger.exception("检索失败")
        return {"success": False, "error": str(e)}


@app.post("/rag/context")
async def get_rag_context(req: RAGSearchRequest):
    """获取格式化的 RAG 上下文（可直接注入 AI 提示）"""
    try:
        context = await rag_service.search_context(req.query, top_k=req.top_k)
        return {"query": req.query, "context": context, "has_context": bool(context)}
    except Exception as e:
        logger.exception("获取上下文失败")
        return {"success": False, "error": str(e)}


@app.delete("/rag/documents/{doc_id}")
async def delete_document(doc_id: str):
    """删除知识库文档"""
    success = rag_service.remove_document(doc_id)
    if success:
        return {"success": True, "message": f"文档 {doc_id} 已删除"}
    else:
        return {"success": False, "message": f"文档 {doc_id} 不存在"}


@app.post("/rag/clear")
async def clear_rag():
    """清空知识库"""
    rag_service.clear()
    return {"success": True, "message": "知识库已清空"}


@app.post("/rag/chat")
async def rag_chat(req: RAGChatRequest):
    """RAG 增强聊天：自动检索知识库并注入上下文"""
    from app.services.file_gen import generate_pptx, generate_docx, generate_chart, generate_image

    user_id = req.user_id or f"rag_{uuid.uuid4().hex[:8]}"

    # 先检索知识库
    rag_context = ""
    if req.use_rag:
        try:
            rag_context = await rag_service.search_context(req.message)
            if rag_context:
                logger.info("RAG 检索到上下文，长度 %d 字符", len(rag_context))
        except Exception:
            logger.exception("RAG 检索失败，继续无知识库模式")

    # 构造增强消息
    enhanced_message = req.message
    if rag_context:
        enhanced_message = (
            f"请参考以下知识库内容回答用户问题。\n\n"
            f"【知识库内容】\n{rag_context}\n\n"
            f"【用户问题】\n{req.message}"
        )

    message = UnifiedMessage(
        platform=Platform.DINGTALK,
        message_id=uuid.uuid4().hex,
        user_id=user_id,
        user_name=user_id,
        content=enhanced_message,
    )

    # 路由处理
    content = req.message.strip()
    for feature in cmd_router.features:
        if feature.matches(content):
            feature_name = feature.name
            reply_text = await feature.handle(message)
            break
    else:
        feature_name = cmd_router.fallback.name
        reply_text = await cmd_router.fallback.handle(message)

    return ChatResponse(
        reply=reply_text,
        user_id=user_id,
        feature=feature_name,
        files=[],
        images=[],
    )


# ---- 管理后台 API 端点 ----

# 功能模块注册表（与 cmd_router.features 保持同步）
_FEATURE_REGISTRY: dict[str, dict] = {
    "report": {"name": "日报周报", "description": "生成日报/周报/月报等总结报告", "enabled": True},
    "meeting": {"name": "会议纪要", "description": "整理会议内容，生成会议纪要", "enabled": True},
    "translate": {"name": "翻译", "description": "多语言翻译", "enabled": True},
    "code": {"name": "代码助手", "description": "代码生成、解释、优化", "enabled": True},
    "chart": {"name": "图表生成", "description": "数据可视化图表生成", "enabled": True},
    "email": {"name": "邮件编辑", "description": "邮件起草和编辑", "enabled": True},
    "reminder": {"name": "智能提醒", "description": "定时提醒任务", "enabled": True},
    "ppt": {"name": "PPT生成", "description": "演示文稿生成", "enabled": True},
    "image_gen": {"name": "图片生成", "description": "AI 图片生成", "enabled": True},
    "summary": {"name": "内容总结", "description": "文本内容总结", "enabled": True},
    "assistant": {"name": "通用助手", "description": "通用问答/对话", "enabled": True},
    "project": {"name": "项目管理", "description": "WBS 拆解与排期偏差预警", "enabled": True},
}

# 内存日志缓冲区（最近 500 条）
_log_buffer: list[dict] = []
_LOG_BUFFER_MAX = 500


class LogHandler(logging.Handler):
    """自定义日志处理器，将日志存入内存缓冲区"""
    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "timestamp": self.formatter.formatTime(record, "%Y-%m-%d %H:%M:%S") if self.formatter else "",
            "level": record.levelname,
            "message": self.format(record),
            "logger": record.name,
        }
        _log_buffer.append(entry)
        if len(_log_buffer) > _LOG_BUFFER_MAX:
            _log_buffer.pop(0)


# 安装日志处理器
_log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_log_handler = LogHandler()
_log_handler.setFormatter(_log_formatter)
_log_handler.setLevel(logging.DEBUG)
logging.getLogger().addHandler(_log_handler)


@app.get("/admin/features")
async def admin_features():
    """获取功能列表"""
    return {"features": [{"name": k, **v} for k, v in _FEATURE_REGISTRY.items()]}


class FeatureToggleRequest(BaseModel):
    name: str
    enabled: bool


class SkillExecuteRequest(BaseModel):
    skill_id: str = Field(..., description="Skill ID")
    message: str = Field(..., description="输入内容")
    user_id: str = Field(default="", description="用户 ID")


class SkillToggleRequest(BaseModel):
    skill_id: str
    enabled: bool


@app.post("/admin/features/toggle")
async def admin_toggle_feature(req: FeatureToggleRequest):
    """切换功能开关"""
    if req.name not in _FEATURE_REGISTRY:
        return {"success": False, "error": f"未知功能: {req.name}"}
    _FEATURE_REGISTRY[req.name]["enabled"] = req.enabled

    # 同步更新路由器
    if req.enabled:
        # 重新启用功能：从备份恢复
        if hasattr(cmd_router, '_disabled_features') and req.name in cmd_router._disabled_features:
            cmd_router.features.append(cmd_router._disabled_features.pop(req.name))
    else:
        # 禁用功能：从路由器移除
        if not hasattr(cmd_router, '_disabled_features'):
            cmd_router._disabled_features = {}
        for feature in list(cmd_router.features):
            if feature.name == req.name:
                cmd_router.features.remove(feature)
                cmd_router._disabled_features[req.name] = feature
                break

    logger.info("功能 %s 已%s", req.name, "启用" if req.enabled else "禁用")
    return {"success": True, "name": req.name, "enabled": req.enabled}


@app.get("/admin/skills")
async def admin_skills():
    """列出本地安装的 Skills."""
    skills = load_installed_skills()
    return {
        "success": True,
        "skills_dir": str(skills_root()),
        "skills": [skill.to_dict() for skill in skills],
    }


@app.post("/admin/skills/reload")
async def admin_skills_reload():
    """重新扫描本地 skills 目录，并刷新聊天路由和工作流 Agent."""
    skill_features = cmd_router.reload_skills()
    global workflow_engine
    workflow_engine = _setup_workflow_engine()
    return {
        "success": True,
        "loaded": len(skill_features),
        "skills": [feature.skill.to_dict() for feature in skill_features],
    }


@app.post("/admin/skills/toggle")
async def admin_skills_toggle(req: SkillToggleRequest):
    """启用或禁用 Skill."""
    try:
        skill = set_skill_enabled(req.skill_id, req.enabled)
        cmd_router.reload_skills()
        global workflow_engine
        workflow_engine = _setup_workflow_engine()
        return {"success": True, "skill": skill.to_dict()}
    except Exception as exc:
        logger.exception("切换 Skill 失败")
        return {"success": False, "error": str(exc)}


@app.post("/admin/skills/install")
async def admin_skills_install(file: UploadFile = File(...)):
    """上传 zip 安装 Skill."""
    suffix = pathlib.Path(file.filename or "").suffix.lower()
    if suffix != ".zip":
        return {"success": False, "error": "仅支持 .zip 格式的 Skill 包"}
    content = await file.read()
    if not content:
        return {"success": False, "error": "Skill 包为空"}

    temp_path = pathlib.Path(tempfile.gettempdir()) / f"skill-{uuid.uuid4().hex}.zip"
    try:
        temp_path.write_bytes(content)
        skill = install_skill_zip(temp_path)
        cmd_router.reload_skills()
        global workflow_engine
        workflow_engine = _setup_workflow_engine()
        return {"success": True, "skill": skill.to_dict()}
    except Exception as exc:
        logger.exception("安装 Skill 失败")
        return {"success": False, "error": str(exc)}
    finally:
        temp_path.unlink(missing_ok=True)


@app.post("/skills/execute")
async def skills_execute(req: SkillExecuteRequest):
    """直接执行一个已安装 Skill."""
    user_id = req.user_id or f"skill_{uuid.uuid4().hex[:8]}"
    message = UnifiedMessage(
        platform=Platform.DINGTALK,
        message_id=uuid.uuid4().hex,
        user_id=user_id,
        user_name=user_id,
        content=req.message,
    )
    try:
        result = await execute_skill(req.skill_id, message, ai_engine)
        return {"success": True, "skill_id": req.skill_id, "reply": result}
    except Exception as exc:
        logger.exception("执行 Skill 失败")
        return {"success": False, "error": str(exc)}


@app.get("/admin/logs")
async def admin_logs(level: str = "", lines: int = 100):
    """获取系统日志"""
    logs = _log_buffer[-lines:] if lines > 0 else _log_buffer
    if level:
        logs = [l for l in logs if l["level"] == level.upper()]
    return {"logs": logs, "total": len(logs), "buffer_size": len(_log_buffer)}


# ---- 多租户 API 端点 ----
from app.core.tenant import tenant_manager, TenantTier


class TenantCreateRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1, description="租户ID")
    name: str = Field(..., min_length=1, description="租户名称")
    tier: str = Field(default="free", description="等级: free/basic/pro/enterprise")
    ai_model: str = Field(default="", description="自定义 AI 模型")
    ai_api_key: str = Field(default="", description="自定义 API Key")


class TenantUpdateRequest(BaseModel):
    name: str = Field(default="", description="租户名称")
    tier: str = Field(default="", description="等级")
    is_active: Optional[bool] = None


@app.get("/admin/tenants")
async def list_tenants():
    """列出所有租户"""
    return {"tenants": tenant_manager.list_tenants()}


@app.post("/admin/tenants")
async def create_tenant(req: TenantCreateRequest):
    """创建租户"""
    try:
        tier = TenantTier(req.tier)
    except ValueError:
        return {"success": False, "error": f"无效等级: {req.tier}，可选: free, basic, pro, enterprise"}

    try:
        config = tenant_manager.create_tenant(
            tenant_id=req.tenant_id,
            name=req.name,
            tier=tier,
            ai_model=req.ai_model,
            ai_api_key=req.ai_api_key,
        )
        return {
            "success": True,
            "tenant": {
                "tenant_id": config.tenant_id,
                "name": config.name,
                "tier": config.tier.value,
                "is_active": config.is_active,
            },
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}


@app.get("/admin/tenants/{tenant_id}")
async def get_tenant(tenant_id: str):
    """获取租户详情"""
    config = tenant_manager.get_tenant(tenant_id)
    if not config:
        return {"success": False, "error": "租户不存在"}
    usage = tenant_manager.get_usage(tenant_id)
    return {
        "tenant": {
            "tenant_id": config.tenant_id,
            "name": config.name,
            "tier": config.tier.value,
            "is_active": config.is_active,
            "ai_model": config.ai_model,
            "quota": {
                "daily_requests": config.quota.daily_requests,
                "max_memory_messages": config.quota.max_memory_messages,
                "max_rag_documents": config.quota.max_rag_documents,
                "max_workflow_executions": config.quota.max_workflow_executions,
                "max_file_upload_mb": config.quota.max_file_upload_mb,
                "features": list(config.quota.features),
            },
            "created_at": config.created_at,
        },
        "usage": usage,
    }


@app.put("/admin/tenants/{tenant_id}")
async def update_tenant(tenant_id: str, req: TenantUpdateRequest):
    """更新租户"""
    kwargs = {}
    if req.name:
        kwargs["name"] = req.name
    if req.tier:
        try:
            kwargs["tier"] = TenantTier(req.tier)
        except ValueError:
            return {"success": False, "error": f"无效等级: {req.tier}"}
    if req.is_active is not None:
        kwargs["is_active"] = req.is_active

    config = tenant_manager.update_tenant(tenant_id, **kwargs)
    if not config:
        return {"success": False, "error": "租户不存在"}
    return {"success": True, "tenant_id": config.tenant_id}


@app.delete("/admin/tenants/{tenant_id}")
async def delete_tenant(tenant_id: str):
    """删除租户"""
    success = tenant_manager.delete_tenant(tenant_id)
    if not success:
        return {"success": False, "error": "租户不存在"}
    return {"success": True, "message": f"租户 {tenant_id} 已删除"}


@app.get("/admin/tenants/{tenant_id}/usage")
async def get_tenant_usage(tenant_id: str):
    """获取租户用量"""
    usage = tenant_manager.get_usage(tenant_id)
    return {"tenant_id": tenant_id, "usage": usage}


# ---- 项目管理 API 端点 ----

class ProjectWBSRequest(BaseModel):
    project_goal: str = Field(..., min_length=1, description="项目目标/需求摘要")
    plan_start: str = Field(..., description="计划开始日期 (YYYY-MM-DD)")
    plan_end: str = Field(..., description="计划结束日期 (YYYY-MM-DD)")
    current_progress: float = Field(default=0, ge=0, le=100, description="当前进度 0-100")
    dependencies: list[str] = Field(default_factory=list, description="依赖项")
    user_role: str = Field(default="bp", description="角色: bp | owner")
    user_id: str = Field(default="", description="用户ID")


class ProjectUpdateRequest(BaseModel):
    project_key: str = Field(..., min_length=1, description="项目唯一标识")
    content: str = Field(..., min_length=1, description="本期进展内容")
    author: str = Field(default="", description="记录人")


@app.post("/project/wbs")
async def project_wbs(req: ProjectWBSRequest):
    """项目管理：WBS 拆解与排期偏差预警"""
    from app.features.project import (
        ProjectManagementFeature,
        _calc_alert,
        _filter_by_role,
        _build_markdown_summary,
        _extract_json,
    )

    feature = ProjectManagementFeature(ai_engine)

    message = UnifiedMessage(
        platform=Platform.DINGTALK,
        message_id=uuid.uuid4().hex,
        user_id=req.user_id or "api",
        user_name=req.user_id or "api",
        content=json.dumps({
            "project_goal": req.project_goal,
            "plan_start": req.plan_start,
            "plan_end": req.plan_end,
            "current_progress": req.current_progress,
            "dependencies": req.dependencies,
            "user_role": req.user_role,
            "user_id": req.user_id,
        }),
    )

    try:
        reply_text = await feature.handle(message)
    except Exception:
        logger.exception("项目管理分析失败")
        return {"success": False, "error": "AI 分析服务暂时不可用"}

    return {
        "success": True,
        "summary": reply_text,
        "history": feature.get_history()[0] if feature.get_history() else None,
    }


@app.get("/project/board")
async def project_board():
    """读取桌面项目管理底层表格，返回项目看板数据"""
    from app.services.project_board import build_project_board

    try:
        board = build_project_board()
        return {"success": True, **board}
    except Exception as exc:
        logger.exception("读取项目管理底层表格失败")
        return {"success": False, "error": str(exc)}


@app.post("/project/update")
async def project_update(req: ProjectUpdateRequest):
    """为项目追加一条双周进展/人工更新记录"""
    from app.services.project_board import add_project_update, build_project_board

    try:
        update = add_project_update(req.project_key, req.content, req.author)
        board = build_project_board()
        return {"success": True, "update": update, "summary": board.get("summary", {})}
    except Exception as exc:
        logger.exception("保存项目进展失败")
        return {"success": False, "error": str(exc)}


@app.post("/project/import")
async def project_import(file: UploadFile = File(...)):
    """导入项目管理底层表格到桌面“项目管理”文件夹"""
    allowed_suffixes = {".xlsx", ".csv", ".tsv"}
    original_name = pathlib.Path(file.filename or "").name
    suffix = pathlib.Path(original_name).suffix.lower()
    if suffix not in allowed_suffixes:
        return {"success": False, "error": "仅支持 .xlsx、.csv、.tsv 表格文件"}

    project_dir = pathlib.Path.home() / "Desktop" / "项目管理"
    project_dir.mkdir(parents=True, exist_ok=True)

    stem = pathlib.Path(original_name).stem or "项目管理导入"
    safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .") or "项目管理导入"
    target = project_dir / f"{safe_stem}{suffix}"
    if target.exists():
        target = project_dir / f"{safe_stem}-{uuid.uuid4().hex[:8]}{suffix}"

    content = await file.read()
    if not content:
        return {"success": False, "error": "导入文件为空"}
    target.write_bytes(content)

    from app.services.project_board import build_project_board

    board = build_project_board()
    return {
        "success": True,
        "filename": target.name,
        "path": str(target),
        "summary": board.get("summary", {}),
    }


@app.get("/project/export")
async def project_export():
    """导出当前项目管理看板为 CSV 表格"""
    from app.services.project_board import build_project_board

    board = build_project_board()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "项目名称",
        "负责人",
        "问题等级",
        "问题原因",
        "推进建议",
        "风险等级",
        "状态",
        "进度",
        "级别",
        "部门",
        "计划周期",
        "项目目标",
        "最新进展",
        "风险/问题",
        "项目价值",
        "人工进展",
        "来源文件",
    ])
    for project in board.get("projects", []):
        updates = " / ".join(
            f"{item.get('timestamp', '')} {item.get('author', '')}: {item.get('content', '')}"
            for item in project.get("updates", [])
        )
        problem_status = project.get("problem_status", {}) or {}
        problem_reasons = " / ".join(
            f"{item.get('label', '')}: {item.get('detail', '')}"
            for item in problem_status.get("issues", [])
        )
        problem_suggestions = " / ".join(problem_status.get("suggestions", []))
        writer.writerow([
            project.get("name", ""),
            project.get("owner", ""),
            problem_status.get("label", ""),
            problem_reasons,
            problem_suggestions,
            project.get("risk_level", ""),
            project.get("status", ""),
            project.get("progress", ""),
            project.get("level", ""),
            project.get("department", ""),
            project.get("period", ""),
            project.get("goal", ""),
            project.get("latest_update", ""),
            project.get("risks", ""),
            project.get("value", ""),
            updates,
            project.get("source", ""),
        ])

    content = "\ufeff" + output.getvalue()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="project-board.csv"'},
    )


@app.get("/project/history")
async def project_history():
    """获取项目管理分析历史记录"""
    from app.services.history_store import get_recent_records

    return {"history": get_recent_records("project", limit=50)}


# ---- 历史搜索 API 端点 ----

class HistorySearchRequest(BaseModel):
    category: str = Field(default="", description="分类: project/chat/search/workflow/rag，留空搜全部")
    query: str = Field(default="", description="搜索关键词")
    start_date: str = Field(default="", description="开始日期 YYYY-MM-DD")
    end_date: str = Field(default="", description="结束日期 YYYY-MM-DD")
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


@app.post("/history/search")
async def search_history(req: HistorySearchRequest):
    """搜索历史记录"""
    from app.services.history_store import search_records

    results = search_records(
        category=req.category or "",
        query=req.query,
        start_date=req.start_date or None,
        end_date=req.end_date or None,
        limit=req.limit,
        offset=req.offset,
    )
    return {"success": True, "results": results, "total": len(results)}


@app.get("/history/search")
async def search_history_get(
    category: str = "",
    query: str = "",
    start_date: str = "",
    end_date: str = "",
    limit: int = 50,
    offset: int = 0,
):
    """搜索历史记录 (GET)"""
    from app.services.history_store import search_records

    results = search_records(
        category=category or "",
        query=query,
        start_date=start_date or None,
        end_date=end_date or None,
        limit=limit,
        offset=offset,
    )
    return {"success": True, "results": results, "total": len(results)}


@app.get("/history/stats")
async def history_stats():
    """获取历史存储统计"""
    from app.services.history_store import get_stats

    return {"success": True, "stats": get_stats()}


@app.delete("/history/{category}")
async def clear_history_category(category: str):
    """清空指定分类的历史记录"""
    from app.services.history_store import clear_category

    clear_category(category)
    return {"success": True, "message": f"分类 {category} 已清空"}


@app.delete("/history/record/{category}/{record_id}")
async def delete_history_record(category: str, record_id: str):
    """删除单条历史记录"""
    from app.services.history_store import delete_record

    ok = delete_record(category, record_id)
    if not ok:
        return {"success": False, "error": "记录不存在"}
    return {"success": True, "message": "记录已删除"}


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    """返回管理后台页面"""
    html_path = pathlib.Path(__file__).parent.parent / "static" / "admin.html"
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/", response_class=HTMLResponse)
async def index():
    """返回 Web 聊天页面."""
    import pathlib
    html_path = pathlib.Path(__file__).parent.parent / "static" / "index.html"
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )
