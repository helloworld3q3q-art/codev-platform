"""多根 ingest 的仓命名空间 —— 单一职责: 给跨仓会碰撞的前端节点打仓维度, 并在读侧还原。

**问题**: 前端节点 id/file 以"仓相对路径"为锚, 无仓维度。两个 extra_repos 有同相对路径文件
(各自 `src/pages/index.vue`)→ 同 id → merge first-wins 静默丢后仓节点 + 边重锚到幸存节点
(幻影扇出), 污染 find_impacted_pages / page_dependencies。

**做法**: 把"属于哪个仓 / 怎么编码 tag / 怎么还原"这个横切概念收在一处, 写侧(`localize`)与
读侧(`resolve`)共用同一 tag↔repo 映射, 不漂移。只命名空间**以仓相对路径为锚**的前端节点
(component/module/route/api_call); 后端端点按 URL 契约全局可比, 不打 tag(否则 `_link` 跨仓按
URL 连前端→后端反被切断)。主仓 tag='' → 既有单仓 id/file 一字不变(零 churn), 单仓项目零影响。

桥(frontend_bridge)与 api_usage 都按 `node.file` 做匹配键: file 打 tag 后变仓内唯一字符串 →
匹配逻辑**无需改动即自动正确**; 唯一需还原 tag 的是按 file 读盘处(经 `resolve`)。
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.schema import AnalyzerResult, NodeKind

# 以仓相对路径为 id 锚、跨仓会碰撞的前端节点 kind(本编码器的职责边界, 与 api_usage 的"用 API 的
# 页面"集 / A3 的"前端代码文件"集是不同概念, 故各自独立, 不强行 DRY)。
_NAMESPACED_KINDS = frozenset({
    NodeKind.FRONTEND_COMPONENT.value,
    NodeKind.FRONTEND_MODULE.value,
    NodeKind.FRONTEND_ROUTE.value,
    NodeKind.FRONTEND_API_CALL.value,
})
_SEP = "::"   # tag 与原 id/file 的分隔: 不会自然出现在 id/相对路径里 → resolve 可逆


class RepoScope:
    """多根仓命名空间编码器。构造期为每仓定一个稳定且仓间唯一的 tag(主仓=''):
      - `localize(result, repo)`  写侧: 原地给该仓前端节点 id/file 打 tag, 边端点同步改写。
      - `resolve(file)`           读侧: tagged file → (来源仓根, 真实相对路径), 供按 file 读盘还原。
    单仓(无 extra)→ 全程 no-op / 透传。纯逻辑(localize/resolve 不做 IO)→ 可脱离 store 单测。"""

    def __init__(self, repos: list[Path]) -> None:
        tags = self._stable_tags(repos)
        self._tag_by_repo: dict[str, str] = {str(r): t for r, t in zip(repos, tags)}
        self._repo_by_tag: dict[str, Path] = {t: r for r, t in zip(repos, tags) if t}

    @staticmethod
    def _stable_tags(repos: list[Path]) -> list[str]:
        """主仓 → ''(既有 id 不变); extra 仓 → 仓 basename(可移植: 任意机器同名 clone 一致),
        basename 撞了缀序号保仓间唯一。"""
        tags: list[str] = []
        used: set[str] = set()
        for i, r in enumerate(repos):
            if i == 0:
                tags.append("")
                used.add("")
                continue
            base, k = (r.name or f"repo{i}"), 1
            tag = base
            while tag in used:
                k += 1
                tag = f"{base}-{k}"
            used.add(tag)
            tags.append(tag)
        return tags

    def localize(self, result: AnalyzerResult, repo: Path) -> None:
        """原地把该仓前端节点 id/file 打 '{tag}::' 前缀(仓内唯一)+ 记 meta['repo_root'], 边端点同步。
        tag='' (主仓 / 单仓)→ no-op。"""
        tag = self._tag_by_repo.get(str(repo), "")
        if not tag:
            return
        pre = tag + _SEP
        idmap: dict[str, str] = {}
        for n in result.nodes:
            if n.kind in _NAMESPACED_KINDS:
                idmap[n.id] = pre + n.id
                n.id = idmap[n.id]
                if n.file:
                    n.file = pre + n.file
                n.meta = n.meta or {}
                n.meta["repo_root"] = str(repo)
        for e in result.edges:
            e.source = idmap.get(e.source, e.source)
            e.target = idmap.get(e.target, e.target)

    def resolve(self, file: str) -> tuple[Path | None, str]:
        """tagged file → (来源仓根, 真实相对路径)。无 tag / 未知 tag → (None, 原样): 调用方回退遍历全仓。"""
        tag, sep, rel = file.partition(_SEP)
        repo = self._repo_by_tag.get(tag) if sep else None
        return (repo, rel) if repo is not None else (None, file)
