"""LoopPolicy —— 每模型的循环行为档(策略对象,agent-provider §1/§4 的落点)。

不同模型指令遵从度差异大(强模型少管、弱模型要紧管),把"管多严"做成可按 provider/模型
调的策略,而非写死在 loop.py。loop 只依赖这个中性类型;具体值由 registry 按 provider 解析
(spec 内置默认 ⊕ config 覆盖)。加模型 / 调参 → 只动 config 或 spec 一行,核心循环零改。

护栏按工具语义三分类(2026-06-05 重构,见 loop.py 顶部):
  - 只读类(read_file / list_dir): distinct 归一化 path 上限 + 总读软顶(读不同文件=进展,近乎不限)。
  - 检索类(codegraph_* / search_docs / impact 等): distinct-args 上限 + 输出侧零增量(结果哈希不变即无进展)。
  - 无效调用类(参数报错 / 路径不存在 / 非法 module): 连续 K 次 → 回灌合法值 + 强制换路。
每个字段都经 registry.loop_policy() 的 _pick 逐字段解析,支持 agent.providers.<name>.loop.<f> 覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoopPolicy:
    max_steps: int = 12              # 单次问答最多循环步数

    # --- 检索类(RETRIEVAL): distinct-args 上限 + 输出侧零增量 ---
    retrieval_distinct_cap: int = 8  # 同一检索工具 distinct-args 执行上限(防变参 thrash)
    no_progress_limit: int = 3       # 连续零增量(结果哈希不变)次数 → 强制收尾
    novelty_check: bool = True       # 是否启用输出侧结果哈希零增量检测(强模型可关,少误伤)

    # --- 只读类(READONLY): distinct 归一化 path 上限 + 总读软顶 ---
    readonly_distinct_cap: int = 20  # 只读工具 distinct 归一化 path 上限(高:读不同文件=确定进展)
    readonly_total_cap: int = 30     # 只读工具总执行次数软顶(含失败尝试;防乱读无关文件刷)

    # --- 无效调用类 ---
    invalid_call_limit: int = 2      # 连续无效调用(参数报错/路径不存在/非法 module)→ 回灌合法值+强制换路

    # --- 读取充分性门(收尾合规) ---
    min_read_for_finish: int = 2     # 收尾时 distinct 成功读到的 path < 此值 → 疑似卡无效调用,提示而非"读够了"

    # --- 查询规划(Phase 7, 每模型策略) ---
    # planner 是模型策略的一部分(强模型自控好可不开 / 软预算即够;弱模型指令遵从差需前摄规划 + 硬封顶)。
    # 不做全局开关,跟 registry 的 _STRONG/_MID/_WEAK 档走,config 可逐字段 per-provider 覆盖。
    planner_enabled: bool = False           # 是否按问题类型规划工具 + 预算 + lane(默认关, 强模型档不开)
    planner_hard_cap_readonly: bool = True   # 超预算后只读类(list_dir/read_file)是否硬拦(弱模型防目录 spelunking)
    # LLM planner(Phase 7 完整版): 用 LLM 分类替关键词(对口语化/无关键词问法关键词只 ~0.27,
    # eval 硬集实测)。**默认全档关**: determinism-first, 多一次 LLM 调用换分类精度的取舍未经 A/B
    # 定论前不默认开; 经 config 逐 provider 开(planner_enabled 为前提, 关时本字段无效)。关键词永远兜底。
    planner_llm_enabled: bool = False

    # --- deprecated 别名: 旧 per_tool_cap → retrieval_distinct_cap(不破存量 config / 测试)---
    per_tool_cap: int | None = None  # deprecated: 构造/读取均映射 retrieval_distinct_cap

    def __post_init__(self) -> None:
        # 显式给了 per_tool_cap(旧构造 / 旧 config)→ 覆盖 retrieval_distinct_cap(向后兼容)。
        # 注: 同时显式给 retrieval_distinct_cap 和 per_tool_cap 时, **以 per_tool_cap 为准**(别名胜出)。
        if self.per_tool_cap is not None:
            object.__setattr__(self, "retrieval_distinct_cap", int(self.per_tool_cap))
        # 让 .per_tool_cap 读取恒等于 retrieval_distinct_cap(deprecated 读别名,不破旧代码/旧断言)。
        object.__setattr__(self, "per_tool_cap", self.retrieval_distinct_cap)
