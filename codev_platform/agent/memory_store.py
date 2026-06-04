"""MemoryStore —— 记忆条目的结构化存储(memory M2 核心)。

存"分层记忆"(org/team/project/personal 作用域),区别于会话(SqlSessionStore)。
对应 memory plan §3.2 数据模型 + §3.4 作用域。

本步范围(M2 结构化层):
- MemoryStore 抽象 + SqlMemoryStore(PG)
- memory_entries 表(带 org_id,作用域隔离)+ write / list-by-scope / supersede / forget
- 读写分离接缝(复用 session_pg 模式):写主库、读副本(可选)
本步不含:向量召回(chroma 侧)+ 冲突消解 + 权限校验 —— 那是 M3/M5,后续接。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_DEFAULT_ORG = "default"

# 作用域取值(plan §二):org / team / project / personal
SCOPES = ("org", "team", "project", "personal")


@dataclass
class MemoryEntry:
    """一条记忆(结构化真值;向量副本在 chroma,M3 接)。"""
    id: str
    scope: str                 # org | team | project | personal
    scope_ref: str             # org='org' / team=team_id / project=project_id / personal=user_id
    owner_user_id: str
    content: str
    org_id: str = _DEFAULT_ORG
    kind: str | None = None     # preference | fact | task | session ...
    topic_key: str | None = None  # 冲突检测键(同 key 跨作用域 = 潜在冲突,M3 用)
    is_redline: bool = False    # org 硬约束,冲突时最高优先
    status: str = "active"      # active | superseded | archived | forgotten
    supersedes: str | None = None
    ttl_at: datetime | None = None  # 遗忘:到期自动 archive(M4);None = 永久
    extra: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None      # M1 任务记忆:关联任务标识(需求/工单/会话任务/Jira issue)
    task_state: str | None = None   # M1:active | blocked | done | archived(None=非任务记忆)


class MemoryStore(ABC):
    """记忆存储抽象。SqlMemoryStore(PG)/ 未来其它实现;上层只依赖本抽象。"""

    @abstractmethod
    def write(self, entry: MemoryEntry) -> str: ...

    @abstractmethod
    def list_scope(self, scope: str, scope_ref: str, org_id: str = _DEFAULT_ORG,
                   limit: int = 100) -> list[MemoryEntry]: ...

    @abstractmethod
    def supersede(self, old_id: str, new_entry: MemoryEntry) -> str: ...

    @abstractmethod
    def forget(self, entry_id: str) -> bool: ...

    @abstractmethod
    def archive(self, entry_id: str) -> bool:
        """显式归档单条(status→archived)。压缩融合时归档原条用(留痕,不物删)。"""
        ...

    @abstractmethod
    def archive_expired(self, org_id: str | None = None) -> int:
        """TTL 到期批量归档(ttl_at < now 且 active → archived)。返回归档条数。"""
        ...
