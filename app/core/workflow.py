"""A2A (Agent-to-Agent) 工作流引擎：Agent 编排、任务派发、协同工作."""
from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

from app.core.ai_engine import AIEngine

logger = logging.getLogger(__name__)


class AgentStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"


@dataclass
class AgentCapability:
    name: str
    description: str
    keywords: list[str] = field(default_factory=list)


@dataclass
class AgentInfo:
    id: str
    name: str
    description: str
    capabilities: list[AgentCapability] = field(default_factory=list)
    status: AgentStatus = AgentStatus.IDLE


@dataclass
class TaskMessage:
    task_id: str
    from_agent: str
    to_agent: str
    instruction: str
    context: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    task_id: str
    agent_id: str
    success: bool
    result: str = ""
    error: str = ""
    artifacts: list[str] = field(default_factory=list)


@dataclass
class WorkflowStep:
    agent_name: str
    instruction_template: str


class WorkflowDefinition:
    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self.steps: list[WorkflowStep] = []

    def add_step(self, agent_name: str, instruction_template: str) -> WorkflowDefinition:
        self.steps.append(WorkflowStep(agent_name, instruction_template))
        return self


class WorkflowAgent(ABC):
    def __init__(self, agent_id: str, name: str, description: str) -> None:
        self.agent_id = agent_id
        self.name = name
        self.description = description
        self.status = AgentStatus.IDLE
        self.capabilities: list[AgentCapability] = []

    def info(self) -> AgentInfo:
        return AgentInfo(
            id=self.agent_id, name=self.name, description=self.description,
            capabilities=list(self.capabilities), status=self.status,
        )

    def add_capability(self, name: str, description: str, keywords: list[str] | None = None) -> None:
        self.capabilities.append(AgentCapability(name=name, description=description, keywords=keywords or []))

    @abstractmethod
    async def execute(self, task: TaskMessage) -> TaskResult:
        ...


class FeatureAgent(WorkflowAgent):
    def __init__(self, agent_id: str, name: str, description: str,
                 handler: Callable[[str, str], Awaitable[str]]) -> None:
        super().__init__(agent_id, name, description)
        self._handler = handler
        self.add_capability(name, description)

    async def execute(self, task: TaskMessage) -> TaskResult:
        self.status = AgentStatus.BUSY
        try:
            full = f"{task.context}\n\n{task.instruction}" if task.context else task.instruction
            result_text = await self._handler(task.task_id, full)
            self.status = AgentStatus.IDLE
            return TaskResult(task_id=task.task_id, agent_id=self.agent_id, success=True, result=result_text)
        except Exception as e:
            logger.exception("Agent %s 失败", self.agent_id)
            self.status = AgentStatus.ERROR
            return TaskResult(task_id=task.task_id, agent_id=self.agent_id, success=False, error=str(e))


class WorkflowEngine:
    def __init__(self, ai_engine: AIEngine) -> None:
        self.ai_engine = ai_engine
        self._agents: dict[str, WorkflowAgent] = {}
        self._workflows: dict[str, WorkflowDefinition] = {}

    def register_agent(self, agent: WorkflowAgent) -> None:
        self._agents[agent.agent_id] = agent
        logger.info("注册 Agent: %s (%s)", agent.name, agent.agent_id)

    def unregister_agent(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)

    def list_agents(self) -> list[AgentInfo]:
        return [agent.info() for agent in self._agents.values()]

    def find_agent_by_capability(self, description: str) -> WorkflowAgent | None:
        for agent in self._agents.values():
            for cap in agent.capabilities:
                if any(kw in description for kw in cap.keywords):
                    return agent
        for agent in self._agents.values():
            for cap in agent.capabilities:
                if cap.name in description or cap.description in description:
                    return agent
        return None

    def register_workflow(self, workflow: WorkflowDefinition) -> None:
        self._workflows[workflow.name] = workflow
        logger.info("注册工作流: %s (%d 步)", workflow.name, len(workflow.steps))

    def list_workflows(self) -> list[dict[str, Any]]:
        return [{"name": wf.name, "description": wf.description,
                 "steps": [{"agent": s.agent_name, "instruction": s.instruction_template} for s in wf.steps]}
                for wf in self._workflows.values()]

    async def execute_workflow(self, workflow_name: str, initial_message: str) -> list[TaskResult]:
        workflow = self._workflows.get(workflow_name)
        if not workflow:
            raise ValueError(f"工作流不存在: {workflow_name}")
        return await self._run_steps(workflow.steps, initial_message)

    async def _run_steps(self, steps: list[WorkflowStep], initial_message: str) -> list[TaskResult]:
        results: list[TaskResult] = []
        ctx = initial_message
        for i, step in enumerate(steps):
            agent = self.find_agent_by_capability(step.agent_name)
            if not agent:
                logger.warning("步骤 %d: 未找到 Agent '%s'", i + 1, step.agent_name)
                continue
            instruction = step.instruction_template.format(context=ctx)
            task = TaskMessage(task_id=uuid.uuid4().hex[:12], from_agent="workflow-engine",
                               to_agent=agent.agent_id, instruction=instruction,
                               context=ctx if i > 0 else "")
            logger.info("步骤 %d/%d: %s", i + 1, len(steps), agent.name)
            result = await agent.execute(task)
            results.append(result)
            if result.success:
                ctx = f"{ctx}\n\n[{agent.name} 结果]:\n{result.result}"
            else:
                logger.error("步骤 %d 失败: %s", i + 1, result.error)
                break
        return results

    async def execute_dynamic(self, user_message: str, user_id: str = "") -> list[TaskResult]:
        agent_list = "\n".join(
            f"- {a.name} ({a.agent_id}): {a.description}" for a in self._agents.values()
        )
        plan_prompt = f"""\
你是任务编排助手。根据用户需求制定任务执行计划。

可用 Agent：
{agent_list}

用户需求：{user_message}

输出 JSON 格式：
{{"plan": [{{"agent": "agent_id", "instruction": "具体任务"}}], "summary": "简述"}}
只输出 JSON，无其他文字。"""
        try:
            plan_text = await self.ai_engine.chat(plan_prompt, user_id=user_id)
            plan_text = plan_text.strip()
            if "```json" in plan_text:
                plan_text = plan_text.split("```json")[1].split("```")[0].strip()
            elif "```" in plan_text:
                plan_text = plan_text.split("```")[1].split("```")[0].strip()
            plan_data = json.loads(plan_text)
            plan_steps = plan_data.get("plan", [])
            steps = [WorkflowStep(agent_name=s.get("agent", ""),
                                  instruction_template=s.get("instruction", user_message))
                     for s in plan_steps]
            if not steps:
                return []
            return await self._run_steps(steps, user_message)
        except Exception as e:
            logger.exception("动态编排失败")
            return [TaskResult(task_id=uuid.uuid4().hex[:12], agent_id="workflow-engine",
                               success=False, error=f"任务编排失败: {e}")]


_workflow_engine: WorkflowEngine | None = None


def init_workflow_engine(ai_engine: AIEngine) -> WorkflowEngine:
    global _workflow_engine
    _workflow_engine = WorkflowEngine(ai_engine)
    return _workflow_engine


def get_workflow_engine() -> WorkflowEngine | None:
    return _workflow_engine
