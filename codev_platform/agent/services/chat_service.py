"""ChatService — 问答编排(application 层).

职责:解析会话 -> 取 provider -> 跑 loop -> 持久化 -> 返回 domain 结果。
不含任何 HTTP / DTO 知识(那是 routes 的事),因此可被 HTTP 同步 / 流式 / CLI 复用。
依赖通过构造注入,便于测试替换。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Callable
from typing import Any

from codev_platform.agent.brain.base import LLMProvider, Message
from codev_platform.agent.context_plan import build_context_plan
from codev_platform.agent.loop import AgentLoop, AgentResult
from codev_platform.agent.policy import LoopPolicy
from codev_platform.agent.prompts import build_code_understanding_system
from codev_platform.agent.recall_service import RecallService
from codev_platform.agent.session import SessionStore
from codev_platform.agent.tools.base import ToolRegistry
from codev_platform.agent.trace import Trace


@dataclass
class ChatOutcome:
    """编排结果(domain 对象,非 HTTP DTO)。"""
    session_id: str
    result: AgentResult


class ChatService:
    def __init__(
        self,
        sessions: SessionStore,
        registry_factory: Callable[[str | None], ToolRegistry],
        provider_factory: Callable[[], LLMProvider],
        default_max_steps: Callable[[], int],
        recall: RecallService | None = None,
        recall_limit: int = 8,
        loop_policy_factory: Callable[[str], LoopPolicy] | None = None,
        prompt_profile_factory: Callable[[str], str | None] | None = None,
        rule_pack_factory: Callable[[str], str | None] | None = None,
        skill_pack_factory: Callable[[str], str | None] | None = None,
        rule_pack_sources_factory: Callable[[str], Any] | None = None,
        skill_pack_sources_factory: Callable[[str], Any] | None = None,
        planner_enabled_factory: Callable[[str], bool] | None = None,
    ) -> None:
        # provider_factory: 每次调用重解析 config(支持运行中切 provider)。
        # registry_factory(project_id): 按请求 project_id 建工具集(P2 多租户路由)。
        # recall: 分层记忆召回(M3),None = 未启用 memory(召回段不注入)。
        # loop_policy_factory(provider_name): 每模型循环策略(步数 / 工具上限),None 时退回
        #   仅用 default_max_steps 的全局默认(向后兼容 + 测试)。
        # prompt/rule/skill factory(provider_name): 每模型 Web-agent instruction profile。
        self._sessions = sessions
        self._registry_factory = registry_factory
        self._provider_factory = provider_factory
        self._default_max_steps = default_max_steps
        self._recall = recall
        self._recall_limit = recall_limit
        self._loop_policy_factory = loop_policy_factory
        self._prompt_profile_factory = prompt_profile_factory
        self._rule_pack_factory = rule_pack_factory
        self._skill_pack_factory = skill_pack_factory
        self._rule_pack_sources_factory = rule_pack_sources_factory
        self._skill_pack_sources_factory = skill_pack_sources_factory
        # planner_enabled_factory(provider_name): Phase 7 查询规划开关(默认 None=关, 行为不变)。
        self._planner_enabled_factory = planner_enabled_factory

    def ask(self, question: str, session_id: str | None = None,
            max_steps: int | None = None, user_id: str = "local",
            project_id: str | None = None, org_id: str = "default",
            task_id: str | None = None, identity: object | None = None) -> ChatOutcome:
        provider = self._provider_factory()  # 缺 key 抛 RuntimeError,由调用层(route)映射

        if session_id and self._sessions.has(session_id, user_id, org_id=org_id):
            sid = session_id
        else:  # 新会话创建时绑当前 project_id(按项目隔离历史的真值源)
            sid = self._sessions.new(user_id, org_id=org_id, project_id=project_id)
        history = self._sessions.get(sid, user_id, org_id=org_id)

        registry = self._registry_factory(project_id)  # 工具按 project_id 路由
        memories = self._recall_memories(org_id, user_id, project_id, question, task_id)
        # M2: 召回记忆 → 分组(redline>task>project>personal>org)+ budget 裁剪 → context_plan,
        # 再注入 system prompt(让模型知道自己在哪个项目 / 为谁 + 遵循已知偏好, 受 budget 控制)。
        # budget_max=recall_limit:召回已 [:recall_limit] 截断, 故此处 budget 主要做分组 +
        # redline-never-cut 兜底;budget_max < recall_limit 时才进一步裁剪(防御纵深)。
        plan = build_context_plan(memories, task_id=task_id, budget_max=self._recall_limit)
        prompt_profile = (
            self._prompt_profile_factory(provider.name)
            if self._prompt_profile_factory is not None else None
        )
        rule_pack = (
            self._rule_pack_factory(provider.name)
            if self._rule_pack_factory is not None else None
        )
        skill_pack = (
            self._skill_pack_factory(provider.name)
            if self._skill_pack_factory is not None else None
        )
        rule_pack_sources = (
            self._rule_pack_sources_factory(provider.name)
            if self._rule_pack_sources_factory is not None else None
        )
        skill_pack_sources = (
            self._skill_pack_sources_factory(provider.name)
            if self._skill_pack_sources_factory is not None else None
        )
        system = build_code_understanding_system(
            project_id=project_id, user_id=user_id, org_id=org_id, context_plan=plan,
            prompt_profile=prompt_profile, rule_pack=rule_pack, skill_pack=skill_pack,
            rule_pack_sources=rule_pack_sources, skill_pack_sources=skill_pack_sources)
        # 每模型策略:有 factory 走它(按 provider 名解析 spec 默认 ⊕ config),否则退回全局 max_steps。
        if self._loop_policy_factory is not None:
            policy = self._loop_policy_factory(provider.name)
        else:
            policy = LoopPolicy(max_steps=self._default_max_steps())
        if max_steps:  # 本次请求显式覆盖步数(策略其余字段不变)
            policy = replace(policy, max_steps=max_steps)
        planner_enabled = (
            self._planner_enabled_factory(provider.name)
            if self._planner_enabled_factory is not None else False
        )
        loop = AgentLoop(provider, registry, policy=policy, planner_enabled=planner_enabled)
        trace = Trace(sid, provider.name, provider.model)
        # M1: 设运行上下文(身份/项目/任务), 供 remember 等工具在 run() 内拿来写 memory;
        # 退出即 reset, 不跨请求泄漏。
        from codev_platform.agent.runctx import RunContext, reset_run_context, set_run_context
        _ctx_token = set_run_context(
            RunContext(user_id=user_id, org_id=org_id, project_id=project_id,
                       task_id=task_id, identity=identity)
        )
        try:
            result = loop.run(question, history=history, trace=trace, system=system)
        finally:
            reset_run_context(_ctx_token)

        # 工具调用流随 assistant 消息持久化(存 extra,经 payload JSONB 往返),
        # 历史会话重载时可回看(前端 ToolFlow)。extra 是中性透传袋, store 无需感知 steps 结构。
        steps_payload = [
            {"n": s.n, "thought": s.thought, "tool": s.tool,
             "args": s.args, "result_summary": s.result_summary}
            for s in result.steps
        ]
        self._sessions.append(
            sid, user_id,
            Message(role="user", content=question),
            Message(role="assistant", content=result.answer,
                    extra={"steps": steps_payload} if steps_payload else {}),
            org_id=org_id,
        )
        return ChatOutcome(session_id=sid, result=result)

    def _recall_memories(self, org_id: str, user_id: str, project_id: str | None,
                         question: str, task_id: str | None = None):
        """召回分层记忆;失败(DB 抖动等)只退化为"不注入记忆",绝不阻断问答。"""
        if self._recall is None:
            return []
        try:
            return self._recall.recall(
                org_id=org_id, user_id=user_id, project_id=project_id,
                query=question, limit=self._recall_limit, task_id=task_id)
        except Exception:  # noqa: BLE001 — 召回非关键路径,失败静默退化
            return []
