from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Optional

from app.core.ai_engine import AIEngine
from app.core.message import UnifiedMessage
from app.features.base import Feature
from app.services.history_store import save_record, get_recent_records

logger = logging.getLogger(__name__)


# ── Prompt 模板 ──────────────────────────────────────────────
PROJECT_PROMPT = """[System] 你是资深项目管理专家。请将输入拆解为结构化WBS，并评估排期偏差。
[Input] 项目目标：{project_goal}
        计划周期：{plan_start} 至 {plan_end}
        当前进度：{current_progress}%
        历史依赖：{dependencies}
[Output] 严格返回JSON，格式：
{"wbs_tree":[{"level":1,"deliverable":"","owner":"","est_hours":0,"deps":[]}], "schedule_alert":{"deviation_days":0, "deviation_pct":0, "alert_level":"green", "critical_path_blocked":false}, "action_suggestion":[]}
[Rules] 
1. WBS层级≤4，每个节点必须有明确交付物与责任人
2. 偏差计算基于线性进度假设，误差范围±10%
3. 仅当关键路径受阻或偏差>15%时标记 critical_path_blocked=true
4. 禁止虚构资源或工时，缺失数据用 null 填充
5. 仅返回 JSON，不要包含任何其他文本"""


def _extract_json(text: str) -> Optional[dict]:
    """从 AI 回复中提取 JSON 对象."""
    s = text.strip()
    # 去掉 ```json / ``` 包裹
    if "```json" in s:
        s = s.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in s:
        s = s.split("```", 1)[1].split("```", 1)[0].strip()
    s = s.strip().strip("`").strip()
    if s.lower().startswith("json"):
        s = s[4:].strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _parse_dependencies(raw) -> list[str]:
    """兼容多种依赖输入格式."""
    if isinstance(raw, list):
        return [str(d) for d in raw]
    if isinstance(raw, str):
        parts = [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
        return parts
    return []


def _calc_alert(
    plan_start: str,
    plan_end: str,
    current_progress: float,
    deps: list[str],
    ai_alert: Optional[dict],
) -> dict:
    """基于规则计算排期偏差（不依赖 AI 返回值）."""
    try:
        start = datetime.strptime(plan_start, "%Y-%m-%d").date()
        end = datetime.strptime(plan_end, "%Y-%m-%d").date()
    except ValueError:
        start = date.today()
        end = date.today()

    total_days = max(1, (end - start).days)
    elapsed = (date.today() - start).days
    expected_progress = max(0, min(100, elapsed / total_days * 100)) if elapsed > 0 else 0
    deviation_pct = current_progress - expected_progress  # 正数=超前, 负数=滞后
    deviation_days = round(abs(deviation_pct) / 100 * total_days, 1)

    # 判定预警等级
    has_critical_block = ai_alert.get("critical_path_blocked", False) if ai_alert else False
    abs_deviation = abs(deviation_pct)

    if has_critical_block or abs_deviation > 15:
        alert_level = "red"
    elif abs_deviation >= 10 or deviation_days >= 2:
        alert_level = "yellow"
    else:
        alert_level = "green"

    return {
        "deviation_days": deviation_days,
        "deviation_pct": round(deviation_pct, 1),
        "alert_level": alert_level,
        "critical_path_blocked": has_critical_block,
    }


def _filter_by_role(wbs_tree: list[dict], user_role: str, user_id: str) -> list[dict]:
    """按角色过滤 WBS 节点."""
    if user_role != "owner":
        return wbs_tree
    return [node for node in wbs_tree if node.get("owner") == user_id]


def _build_markdown_summary(
    wbs_tree: list[dict],
    alert: dict,
    project_goal: str,
    plan_start: str,
    plan_end: str,
    suggestions: list[str],
    user_role: str,
) -> str:
    """构建 Markdown 格式摘要."""
    alert_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    emoji = alert_emoji.get(alert["alert_level"], "⚪")

    lines = [
        f"## 📊 项目管理分析报告",
        "",
        f"**项目目标**: {project_goal}",
        f"**计划周期**: {plan_start} ~ {plan_end}",
        f"**预警等级**: {emoji} **{alert['alert_level'].upper()}**",
        f"**偏差天数**: {alert['deviation_days']} 天",
        f"**偏差百分比**: {alert['deviation_pct']}%",
        f"**关键路径阻塞**: {'⚠️ 是' if alert['critical_path_blocked'] else '✅ 否'}",
        "",
        "### 📋 WBS 结构",
        "",
    ]

    for node in wbs_tree:
        indent = "  " * (node.get("level", 1) - 1)
        deliverable = node.get("deliverable", "未命名")
        owner = node.get("owner", "待定")
        hours = node.get("est_hours", 0)
        deps = ", ".join(node.get("deps", [])) or "无"
        lines.append(f"{indent}- **{deliverable}** (责任人: {owner}, 工时: {hours}h, 依赖: {deps})")

    if suggestions:
        lines.append("")
        lines.append("### 💡 建议动作")
        for s in suggestions:
            lines.append(f"- {s}")

    if user_role == "owner":
        lines.append("")
        lines.append("> ℹ️ 当前以 **负责人** 角色查看，仅显示属于您的 WBS 节点。")

    return "\n".join(lines)


class ProjectManagementFeature(Feature):
    """智能项目管理：WBS 拆解与排期偏差预警."""

    name = "项目管理"
    keywords = [
        "项目管理", "WBS", "拆解", "排期", "进度",
        "里程碑", "关键路径", "project", "甘特图",
        "项目计划", "项目分析", "偏差预警",
    ]

    system_prompt = PROJECT_PROMPT

    def __init__(self, ai_engine: AIEngine) -> None:
        super().__init__(ai_engine)

    async def handle(self, message: UnifiedMessage) -> str:
        """处理项目管理请求."""
        content = message.content.strip()

        # 1. 尝试提取 JSON 输入
        project_data = self._extract_input(content)

        # 2. 校验必填字段
        missing = self._validate(project_data)
        if missing:
            return (
                f"⚠️ 缺少必填信息：{'、'.join(missing)}\n\n"
                f"请补全以下信息后重试：\n"
                f"- 项目目标（project_goal）\n"
                f"- 计划开始日期（plan_start, 格式 YYYY-MM-DD）\n"
                f"- 计划结束日期（plan_end, 格式 YYYY-MM-DD）\n"
                f"- 当前进度（current_progress, 0-100）\n\n"
                f"示例输入：\n"
                f'```json\n'
                f'{{"project_goal":"开发用户登录模块","plan_start":"2026-06-01","plan_end":"2026-06-30","current_progress":40,"dependencies":["需求文档","UI设计稿"],"user_role":"bp","user_id":"admin"}}\n'
                f'```'
            )

        # 3. 修正进度边界
        progress = max(0, min(100, float(project_data.get("current_progress", 0))))

        # 4. 构建 AI 提示
        deps_text = ", ".join(project_data.get("dependencies", [])) or "无"
        prompt = self.system_prompt.format(
            project_goal=project_data.get("project_goal", ""),
            plan_start=project_data.get("plan_start", ""),
            plan_end=project_data.get("plan_end", ""),
            current_progress=progress,
            dependencies=deps_text,
        )

        # 5. 调用 AI 引擎
        try:
            ai_response = await self.ai.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            ai_data = _extract_json(ai_response)
        except Exception:
            logger.exception("AI 引擎调用失败")
            return "❌ AI 分析服务暂时不可用，请稍后重试。"

        if ai_data is None:
            logger.warning("AI 返回非 JSON，原始内容: %s", ai_response[:200])
            return (
                f"❌ AI 解析结果格式异常，请重试。\n\n"
                f"原始回显（前 500 字符）:\n```\n{ai_response[:500]}\n```"
            )

        # 6. 提取/计算
        wbs_tree = ai_data.get("wbs_tree", [])
        ai_alert = ai_data.get("schedule_alert", {})
        suggestions = ai_data.get("action_suggestion", [])
        user_role = project_data.get("user_role", "bp")
        user_id = project_data.get("user_id", message.user_id)

        # 7. 偏差规则计算（覆盖 AI 的 alert_level）
        final_alert = _calc_alert(
            project_data["plan_start"],
            project_data["plan_end"],
            progress,
            project_data.get("dependencies", []),
            ai_alert,
        )

        # 8. 权限过滤
        filtered_wbs = _filter_by_role(wbs_tree, user_role, user_id)

        # 9. 构建 Markdown 摘要
        summary = _build_markdown_summary(
            filtered_wbs,
            final_alert,
            project_data["project_goal"],
            project_data["plan_start"],
            project_data["plan_end"],
            suggestions,
            user_role,
        )

        # 10. 保存历史（持久化到文件）
        save_record("project", {
            "project_goal": project_data["project_goal"],
            "plan_start": project_data["plan_start"],
            "plan_end": project_data["plan_end"],
            "current_progress": progress,
            "alert_level": final_alert["alert_level"],
            "deviation_days": final_alert["deviation_days"],
            "deviation_pct": final_alert["deviation_pct"],
            "wbs_count": len(filtered_wbs),
            "wbs_tree": filtered_wbs,
            "suggestions": suggestions,
            "user_role": user_role,
            "user_id": user_id,
        })

        return summary

    def _extract_input(self, content: str) -> dict:
        """从消息中提取项目数据."""
        # 尝试解析 JSON 代码块
        data = _extract_json(content)
        if data and isinstance(data, dict):
            return data

        # 尝试解析键值对形式的自然语言
        result: dict = {}
        lines = content.split("\n")
        key_map = {
            "项目目标": "project_goal",
            "项目名称": "project_goal",
            "project_goal": "project_goal",
            "project goal": "project_goal",
            "计划开始": "plan_start",
            "plan_start": "plan_start",
            "plan start": "plan_start",
            "计划结束": "plan_end",
            "plan_end": "plan_end",
            "plan end": "plan_end",
            "当前进度": "current_progress",
            "进度": "current_progress",
            "current_progress": "current_progress",
            "current progress": "current_progress",
            "依赖": "dependencies",
            "dependencies": "dependencies",
            "角色": "user_role",
            "user_role": "user_role",
            "user role": "user_role",
            "用户ID": "user_id",
            "user_id": "user_id",
            "user id": "user_id",
        }

        for line in lines:
            for key, field in key_map.items():
                if key in line.lower() or key.replace(" ", "") in line.lower().replace(" ", ""):
                    parts = line.split(":", 1) if ":" in line else line.split("：", 1)
                    if len(parts) == 2:
                        value = parts[1].strip()
                        if field == "dependencies":
                            result[field] = _parse_dependencies(value)
                        elif field == "current_progress":
                            try:
                                result[field] = float(value.replace("%", "").strip())
                            except ValueError:
                                pass
                        else:
                            result[field] = value
                    break

        return result

    def _validate(self, data: dict) -> list[str]:
        """校验必填字段."""
        required = ["project_goal", "plan_start", "plan_end", "current_progress"]
        missing = []
        for field in required:
            if field not in data or data[field] in (None, "", 0):
                missing.append(field)
        return missing

    def get_history(self) -> list[dict]:
        """获取分析历史记录（从持久化存储）."""
        return get_recent_records("project", limit=50)
