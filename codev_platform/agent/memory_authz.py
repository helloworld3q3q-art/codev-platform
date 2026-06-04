"""memory 写入授权 + topic_key 归一 —— route / remember 工具 / 迁移脚本共用的单一真值源。

P0(dev-agent-memory MCP 前门前置修复, design §七)抽出三件, 堵三个真缺口:
- ``make_topic_key``: 三写入端统一 slug。否则 "Dark Mode" / "dark-mode" / "dark_mode" 各成一条、
  永不参与冲突消解, project scope 被同主题重复条灌满(缺口 1)。
- ``scope_decision``: 作用域写授权(原 ``routes/memory.py`` 内联), 抽出供 remember 工具复用 ——
  "工具写" 与 "路由写" 走同一道闸 + 同一 ``audit_access``(缺口 2)。
- ``redline_write_allowed``: redline 单独写闸(仅 org admin), 防持普通 member / IDE token 冒造
  org 硬约束污染冲突消解(缺口 3)。

纯函数 + 薄 store 读, 无 HTTP / DTO 依赖。``cfg`` 由调用方传入(route 用 ``load_config()`` 可被
测试 monkeypatch; 工具自取 config), 不在本模块绑定 config 名, 保留调用方对 cfg 的主权。
"""
from __future__ import annotations

import re

from codev_platform.core.acl import AccessDecision, can_access, memory_scope_access
from codev_platform.core.rbac import memory_scope_decision, role_allows

# 连续连字符折叠用。字符级保留判定走 str.isalnum()(Unicode-aware), 见 make_topic_key。
_DASH_RUN = re.compile("-+")


def make_topic_key(raw: str | None) -> str | None:
    """任意 topic 标签 → 稳定 slug。三写入端(remember 工具 / web 路由 / 迁移脚本)必须调同一
    函数, 否则同偏好被判两条不冲突、both 注入自相矛盾(design §五)。

    规则: strip → 小写 → 非字母数字字符折成单连字符 → 去首尾连字符。空 / 全符号 → None。
    保留判定用 ``str.isalnum()``(Unicode-aware): 中文 / 日文假名 / 韩文 / 带音标拉丁 / 数字
    全部保留, 仅符号 / 空白 / 下划线当分隔符 —— 避免非中文脚本主题被吞成空 key 而漏去重。
    """
    if not raw:
        return None
    folded = "".join(c if c.isalnum() else "-" for c in raw.strip().lower())
    s = _DASH_RUN.sub("-", folded).strip("-")
    return s or None


def scope_decision(cfg, org_id, user_id, scope, scope_ref, ident) -> AccessDecision:
    """作用域写访问判定(单一真值源, route + remember 工具共用)。

    - **project**: 由 P1 项目闸 ``can_access``(token allowlist + org)决定 —— project memory 是
      项目资源, 与全栈 project 门禁同源; **不叠加 M5 RBAC project_role**(避免双重门禁打架)。
    - **org/team**: 有 RBAC store → 查真实 Membership 走 ``memory_scope_decision``(角色制);
      否则回退 interim ``memory_scope_access``(token 模式 org/team 兜底 deny)。
    - **personal**: 两路均做 owner==本人 自校验, 同源。
    """
    if scope == "project":
        return can_access(cfg, ident, scope_ref)
    from codev_platform.agent import deps  # lazy: 破 deps -> tools -> remember -> memory_authz 环
    store = deps.get_rbac_store()
    if store is not None:
        m = store.fetch_membership(org_id, user_id, None)
        return memory_scope_decision(scope, scope_ref, user_id, m)
    return memory_scope_access(cfg, ident, scope, scope_ref)


def redline_write_allowed(org_id, user_id, ident) -> AccessDecision:
    """redline 写闸: 仅 org admin 可写 org 硬约束。

    - 有 RBAC store: ``org_role`` 覆盖 admin 动作 → allow, 否则 deny。
    - 无 RBAC store(interim / dev): 一律 deny —— redline 只经 web 管理面 / 迁移脚本**直写 store**
      (绕过本闸), 不经 IDE / agent token 路径。冒造 org 硬约束代价高 → 宁可 deny 不误放。
    """
    from codev_platform.agent import deps  # lazy(同上)
    store = deps.get_rbac_store()
    if store is not None and user_id:
        m = store.fetch_membership(org_id, user_id, None)
        if role_allows(m.org_role, "admin"):
            return AccessDecision(True, f"redline: org_role={m.org_role} allows admin")
        return AccessDecision(False, f"redline write requires org admin (role={m.org_role})")
    return AccessDecision(False, "redline write denied: no RBAC store (use web admin / migration)")
