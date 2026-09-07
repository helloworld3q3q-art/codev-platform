"""agent prompt 文案. 独立成文件:prompt 调优频繁,且后续可能 per-provider 微调,
与 loop 引擎逻辑分开便于迭代。
"""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any

from codev_platform.agent.context_plan import GROUP_ORDER

# A 能力(只读代码理解)系统提示
CODE_UNDERSTANDING_SYSTEM = """你是 codev-platform 的只读代码理解 agent。回答关于本仓代码 / 架构 / 规则的问题。

工具选型(按需调用,不要瞎调):
- 找代码符号定义(函数/类/方法)+ 位置签名 → codegraph_search
- 找符号的调用方 / 影响面 → codegraph_callers;找它引用了谁 → codegraph_callees
- 找规则 / 设计文档 / 事故复盘 / 操作手册 → search_docs
- 改某节点(表/端点/函数/前端)的跨层影响面 + 风险等级 → impact_analysis(统一图谱, 优先)
- 表被谁读/写(函数/端点/前端)→ table_usage;端点被哪些前端调 → api_callers;前端页依赖什么 → page_dependencies
- 当前任务值得跨轮/跨会话记住的目标/约束/决策/阻塞/验收 → remember(写进长期记忆, 后续对话自动召回);判断"这条以后还需要"时才记, 简洁一条

规则:
1. 用工具拿到证据再回答,不要凭空编造函数名 / 字段。
2. 答案带证据:引用文件:行号 / 表名 / 端点名。
3. 拿到足够信息就给最终答案,不要无谓多轮。
4. **查不到就如实说"未找到",严禁推测 / 脑补 / "通常应该是…"**。宁可答"索引里没有",不要编一个看似合理的答案。
5. **不要混淆"工具名"和"代码符号名"**:你可用的工具(如 table_usage)是 agent 能力,与用户问的仓库代码符号是两回事。用户问某符号时,照常用 codegraph_search 查它,别因为它名字像工具名就断言"这是我的内置工具"。
6. 若 codegraph_search 换几个写法都查不到,可能是索引未覆盖该文件(新代码未重建索引),如实说明"未在索引中找到,可能索引未更新",不要据此下"不存在"的结论。
7. **同一个子问题最多换 2-3 种查法**;若仍无果,**立刻用已掌握的证据给出部分答案**,并说明"哪部分查不到 / 受索引限制",绝不为一个细节反复查到耗尽步数。多跳间接调用(经辅助函数中转)codegraph 可能连不起来,属已知限制。
8. 一个问题含多个子问题时,**已解决的部分先答**,未解决的标注清楚,不要因一个子问题卡住而放弃整个回答。
"""


EXPLICIT_TOOL_SELECTION_OVERLAY = """【模型专用补充:显式工具选型】

工具名必须照写,不要发明新工具名。优先用能一次拿到证据的工具,减少手动翻文件。

- 跨层影响/数据流: `impact_analysis` 优先;查表用 `table_usage`,查端点消费者用 `api_callers`,查页面依赖用 `page_dependencies`。
- 代码定位: 不确定实现在哪先用 `code_recall`;已知符号名用 `codegraph_search`;多跳调用链用 `codegraph_trace`;只看一跳用 `codegraph_callers` / `codegraph_callees`。
- 规则/设计/操作历史: 用 `search_docs`,定位到具体文档后再 `read_file`。
- 文件: 只有已有具体路径才 `read_file`;不知道目录或路径报错时才 `list_dir`。
- 记忆: 用户明确偏好、长期约定、关键决策、阻塞或验收条件才 `remember`,一次只记一条。

顺序: 影响面题先 graph 工具;文档题先 `search_docs`;代码题先 `code_recall` 或 `codegraph_search`;够用就回答。找不到时说明未找到或索引可能未覆盖,不要猜文件、猜函数。
"""


_PROMPT_PROFILES = {
    "explicit_tool_selection": EXPLICIT_TOOL_SELECTION_OVERLAY,
}


_RULE_PACKS = {
    # Web agent 自己的 instruction 规则。不要默认读 codev_platform/resources/rules:
    # 那一套是 sync 给 Codex/Claude 开发工作区的。
    "mcp_first_code_understanding": (
        "agent-rules:mcp-first-code-understanding.md",
    ),
}


_SKILL_PACKS = {
    # Web agent 自己的 skill。不要默认读 codev_platform/resources/skills:
    # 那一套可能包含 shell / AskUserQuestion / Claude Code 专用流程。
    "code_understanding": ("agent-skills:code-understanding.md",),
}


def _prompt_profile_text(prompt_profile: str | None) -> str | None:
    """按 profile 名取模型专用 prompt overlay。未知 profile 显式报错,避免静默拼错配置。"""
    if not prompt_profile:
        return None
    text = _PROMPT_PROFILES.get(prompt_profile)
    if text is None:
        raise ValueError(
            f"未知 prompt_profile: {prompt_profile}; 可选:{', '.join(sorted(_PROMPT_PROFILES))}"
        )
    return text


def _read_pack_source(source: str, kind: str) -> tuple[str, str]:
    """读取 rule/skill source。

    source 支持:
    - agent-rules:<file-or-dir>  -> codev_platform/agent/instructions/rules
    - agent-skills:<file-or-dir> -> codev_platform/agent/instructions/skills
    - rules:<file-or-dir>        -> codev_platform/resources/rules(仅显式配置时使用)
    - skills:<file-or-dir>       -> codev_platform/resources/skills(仅显式配置时使用)
    - builtin:<skill-name>  -> 内置 Web-agent skill
    - 绝对/相对文件系统路径;目录会按 kind 展开 md / SKILL.md
    """
    if source.startswith("builtin:"):
        raise ValueError(f"未知 builtin skill source: {source}; Web agent 默认 skill 已迁到 agent-skills:")

    if source.startswith("agent-rules:") or source.startswith("agent-skills:"):
        prefix, rel = source.split(":", 1)
        subdir = "rules" if prefix == "agent-rules" else "skills"
        base = files("codev_platform") / "agent" / "instructions" / subdir
        target = base / rel
        if target.is_dir():
            pattern = ".md"
            children = sorted(c for c in target.iterdir() if c.name.endswith(pattern))
            chunks = [f"## {source}/{child.name}\n{child.read_text(encoding='utf-8')}"
                      for child in children]
            return source, "\n\n".join(chunks)
        return source, target.read_text(encoding="utf-8")

    if source.startswith("rules:") or source.startswith("skills:"):
        prefix, rel = source.split(":", 1)
        base = files("codev_platform") / "resources" / prefix
        target = base / rel
        if target.is_dir():
            chunks: list[str] = []
            if kind == "rule":
                children = sorted(c for c in target.iterdir() if c.name.endswith(".md"))
            else:
                children = sorted(c for c in target.iterdir() if c.name == "SKILL.md" or c.name.endswith(".md"))
            for child in children:
                chunks.append(f"## {source}/{child.name}\n{child.read_text(encoding='utf-8')}")
            return source, "\n\n".join(chunks)
        return source, target.read_text(encoding="utf-8")

    path = Path(source).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if path.is_dir():
        if kind == "rule":
            children = sorted(path.glob("*.md"))
        else:
            children = sorted(path.glob("SKILL.md")) + sorted(path.glob("*/SKILL.md"))
        chunks = [f"## {child}\n{child.read_text(encoding='utf-8')}" for child in children]
        return str(path), "\n\n".join(chunks)
    return str(path), path.read_text(encoding="utf-8")


def _as_sources(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _rule_pack_text(rule_pack: str | None, sources: Any = None) -> str | None:
    """按规则包名读取 package resources/rules。规则包用于 Web agent system prompt 注入。"""
    if not rule_pack:
        return None
    resolved_sources = _as_sources(sources)
    if resolved_sources is None:
        names = _RULE_PACKS.get(rule_pack)
        if names is None:
            raise ValueError(f"未知 rule_pack: {rule_pack}; 可选:{', '.join(sorted(_RULE_PACKS))}")
        resolved_sources = list(names)
    sections = ["【规则包】"]
    for source in resolved_sources:
        label, text = _read_pack_source(source, "rule")
        sections.append(f"## {label}\n{text}")
    return "\n\n".join(sections)


def _skill_pack_text(skill_pack: str | None, sources: Any = None) -> str | None:
    """按技能包名取 Web-agent 安全 skill。不要默认注入 Claude/Codex 专用 shell skill。"""
    if not skill_pack:
        return None
    resolved_sources = _as_sources(sources)
    if resolved_sources is None:
        skills = _SKILL_PACKS.get(skill_pack)
        if skills is None:
            raise ValueError(f"未知 skill_pack: {skill_pack}; 可选:{', '.join(sorted(_SKILL_PACKS))}")
        resolved_sources = list(skills)
    sections = ["【技能包】"]
    for source in resolved_sources:
        label, text = _read_pack_source(source, "skill")
        sections.append(f"## {label}\n{text}")
    return "\n\n".join(sections)


# 记忆压缩融合(M4)系统提示:把同 topic 多条记忆融合成一条
MEMORY_FUSION_SYSTEM = """你是记忆压缩器。把同一主题下的多条记忆融合成一条简洁、无冗余、不丢关键信息的记忆。
规则:
1. 只输出融合后的一条记忆正文,不要解释、不要列表标记、不要任何前后缀。
2. 保留所有关键事实 / 偏好 / 约束;表述冲突时以更具体或更新的为准。
3. 严禁编造原文没有的信息。
4. 用中文,一到两句话为宜。
"""


# context_plan 分组 → 中文小标题(prompt 展示用)。
_GROUP_LABEL = {
    "redline": "组织硬约束(redline, 任何情况不得违背)",
    "task": "当前任务记忆",
    "project": "项目记忆",
    "personal": "个人偏好",
    "org": "组织记忆",
}


def _format_context_plan(plan) -> str:
    """把 ContextPlan(M2 分组 + budget 裁剪后)排成 prompt 段:按优先级分组展示, redline 标硬约束。

    只读 plan.groups / GROUP_ORDER, 不碰 DB / 召回逻辑(prompt 文案与 context 计划解耦)。
    """
    lines = ["【已知记忆(按优先级分组召回, 供回答时遵循)】"]
    for g in GROUP_ORDER:
        items = plan.groups.get(g)
        if not items:
            continue
        lines.append(f"· {_GROUP_LABEL.get(g, g)}:")
        for m in items:
            lines.append(f"  - {m.content}")
    lines.append("遵循上述记忆;标 redline 的是组织硬约束, 任何情况不得违背, 冲突时以 redline 为准。")
    return "\n".join(lines)


def _project_display_name(project_id: str) -> str | None:
    """取项目 display_name 作 prompt 自描述(让模型能判断"这功能像不像本项目的")。

    best-effort:读 platform_meta meta.json,缺失 / 任何异常 → None(自描述是锦上添花,
    绝不因此影响 prompt 构建)。lazy import 破环(prompts ← tools._project)。
    """
    try:
        from codev_platform.agent.tools._project import display_name_of
        return display_name_of(project_id)
    except Exception:  # noqa: BLE001
        return None


def build_code_understanding_system(
    project_id: str | None = None,
    user_id: str | None = None,
    org_id: str | None = None,
    context_plan=None,
    prompt_profile: str | None = None,
    rule_pack: str | None = None,
    skill_pack: str | None = None,
    rule_pack_sources: Any = None,
    skill_pack_sources: Any = None,
) -> str:
    """在基础 prompt 前注入当前请求上下文 (org/user/project) + 召回的分层记忆,让模型
    "知道自己在为谁、在哪个组织/项目工作"并遵循已知偏好/约束。工具已按 project_id 路由
    (查对应项目的库),本注入让模型的自我认知与之一致 —— 否则问"现在哪个项目"会照写死
    prompt 瞎猜。也是权限的认知地基。context_plan 由 build_context_plan(M2 分组+budget)
    产出, 空则不注入。
    """
    has_mem = context_plan is not None and not context_plan.is_empty()
    profile_text = _prompt_profile_text(prompt_profile)
    rule_text = _rule_pack_text(rule_pack, rule_pack_sources)
    skill_text = _skill_pack_text(skill_pack, skill_pack_sources)
    if not (project_id or user_id or org_id or has_mem or profile_text or rule_text or skill_text):
        return CODE_UNDERSTANDING_SYSTEM
    parts: list[str] = []
    if project_id or user_id or org_id:
        ctx_lines = ["【当前会话上下文】"]
        if org_id:
            ctx_lines.append(f"- 组织(org):{org_id}")
        if user_id:
            ctx_lines.append(f"- 用户(user):{user_id}")
        if project_id:
            _dn = _project_display_name(project_id)
            _label = f"{project_id}({_dn})" if _dn else project_id
            ctx_lines.append(
                f"- 项目(project):{_label}"
                "(你的检索工具已绑定到此项目,所有 codegraph/impact/search_docs"
                "查的都是这个项目的数据;问'现在哪个项目'就答它)"
            )
            # 防"绑错项目空转 + 凭空捏造": 索引只覆盖本项目, 越界即如实收尾, 不堆工具不编造。
            ctx_lines.append(
                "- ⚠️ 索引范围:你只能看到**本项目**的代码与文档,看不到其它项目。"
                "若用户问的功能 / 页面 / 模块在本项目检索 2 次仍找不到、且不像属于本项目 → "
                f"**立刻停止换词重搜**,如实说「在本项目 {project_id} 里没找到」,并提示「可能属于"
                "别的项目(平台自身 / 其它业务仓),换 project_id 重连后再查」。"
                "**绝不凭空捏造不存在的文件 / 页面 / 符号来硬凑答案** —— 没有就说没有。"
            )
        else:
            ctx_lines.append("- 项目:未指定(工具按进程默认仓)")
        parts.append("\n".join(ctx_lines))
    if has_mem:
        parts.append(_format_context_plan(context_plan))
    parts.append(CODE_UNDERSTANDING_SYSTEM)
    if profile_text:
        parts.append(profile_text)
    if rule_text:
        parts.append(rule_text)
    if skill_text:
        parts.append(skill_text)
    return "\n\n".join(parts)
