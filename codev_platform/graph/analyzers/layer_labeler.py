"""架构分层标注器接口 —— 把 LLM 不确定性隔离在纯数据契约后(A2 核心隔离点, 对称 domain_labeler)。

ArchLayerAnalyzer 调本接口给 file 标架构层角色。入参/出参全确定性 dataclass(不暴露 brain),
聚类 / grounding 数据准备 / 解析回填全在 analyzer 侧可测, LLM 措辞差异碰不到这些路径。

closed-world 进类型: file 编号成 ref(f1/f2)+ 给确定性事实(import/符号/表/endpoint), labeler 只能
① 引清单内 ref ② 选 LAYER_ROLES 枚举里的 layer —— 越界 ref 或越界 layer, analyzer 解析时剔除。
layer 是**固定枚举词表**(比 A1 自由命名域更易 ground)。

实现(两个):
- FakeLayerLabeler(测试 / A2-1): 按确定性启发查表, 零网络, 覆盖 analyzer 全确定性路径。
- BrainLayerLabeler(A2-2, 生产): 渲染 prompt → brain chat → 解析(待 A2-2)。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# 后端架构层角色固定词表(枚举闭集; labeler 出界即剔)。少量、正交于业务域。
LAYER_ROLES: frozenset[str] = frozenset({
    "controller", "service", "repository", "domain_model",
    "util", "config", "adapter", "gateway",
})
# 前端架构层角色词表(前后端分层语义不同, 各用各词表)。page/component 用确定性 is_page 辅助判别。
FRONTEND_LAYER_ROLES: frozenset[str] = frozenset({
    "page", "component", "api", "store", "hook", "util", "config",
})
# 越界校验全集(analyzer _to_soft 宽松校验 ∈ ALL; labeler 按 file.is_frontend 精确选子词表)。
ALL_LAYER_ROLES: frozenset[str] = LAYER_ROLES | FRONTEND_LAYER_ROLES


@dataclass(frozen=True)
class FileFact:
    """喂给标注器的一个 file 的确定性事实包(closed-world)。ref 是给模型的稳定短 ID, 非真 node.id。"""

    ref: str                      # "f1" —— closed-world 引用锚点(造不出新 ref = 造不出新文件)
    path: str                     # 相对路径(目录结构是层先验: services/ repositories/ ...)
    imports_out: int = 0          # import 出邻居数(依赖谁)
    imports_in: int = 0           # 被 import 数(被依赖多 → 偏 util/domain_model)
    has_endpoint: bool = False    # 含 backend_endpoint(偏 controller/gateway)
    reads_tables: int = 0         # reads/writes 表数(偏 repository)
    defines_classes: int = 0
    defines_functions: int = 0
    is_frontend: bool = False     # 前端文件(节点全 frontend_*)→ 用 FRONTEND_LAYER_ROLES 词表
    is_page: bool = False         # 前端: 确定性 is_page(dependency-cruiser 产)→ 强先验 page


@dataclass(frozen=True)
class LayerRequest:
    """一批 file 的标注请求(按目录/包成批, 一次省 token)。"""

    batch_id: str                 # 目录/包路径(聚类单位)
    files: tuple[FileFact, ...]


@dataclass(frozen=True)
class LayerLabel:
    """标注器对一批 file 的角色归类。"""

    batch_id: str
    # (file_ref, layer_role) 对; layer 必 ∈ LAYER_ROLES 且 ref 必 ∈ 输入, analyzer 校验越界剔除。
    roles: tuple[tuple[str, str], ...] = ()


@runtime_checkable
class LayerLabeler(Protocol):
    """架构层标注契约。入纯数据出纯数据, LLM 隔离在实现侧。

    label: 批量标注; 失败 fail-soft(整批放弃则该条 roles=(), 不抛)。返回按 batch_id 对齐, 不依赖顺序。
    """

    def label(self, batch: list[LayerRequest]) -> list[LayerLabel]: ...


class FakeLayerLabeler:
    """确定性测试标注器: 按事实启发选角色(零网络), 覆盖 analyzer 全确定性路径。

    启发**故意简单**, 只为 A2-1 跑通确定性管线, 不追准确率(真准确率靠 A2-2 BrainLayerLabeler 验收):
    - has_endpoint → controller
    - reads_tables>0 且无 endpoint → repository
    - 否则按路径关键词(controller/service/repository/...)命中 LAYER_ROLES → 对应角色
    - 都不命中 → service(中性默认)
    """

    signature = "fake-layer-v1"   # 缓存键的一部分(对称 DomainLabeler.signature)

    def label(self, batch: list[LayerRequest]) -> list[LayerLabel]:
        out: list[LayerLabel] = []
        for req in batch:
            roles = tuple((f.ref, self._role_for(f)) for f in req.files)
            out.append(LayerLabel(req.batch_id, roles))
        return out

    @staticmethod
    def _role_for(f: FileFact) -> str:
        if f.is_frontend:
            return FakeLayerLabeler._frontend_role(f)
        if f.has_endpoint:
            return "controller"
        if f.reads_tables > 0:
            return "repository"
        low = f.path.lower()
        for kw in ("controller", "service", "repository", "adapter", "gateway", "config", "util"):
            if kw in low and kw in LAYER_ROLES:
                return kw
        return "service"

    @staticmethod
    def _frontend_role(f: FileFact) -> str:
        if f.is_page:                              # is_page 确定性强先验
            return "page"
        low = f.path.lower()
        for kw, role in (("/apis/", "api"), ("/services/", "api"), ("hook", "hook"),
                         ("use", "hook"), ("/store", "store"), ("/model", "store"),
                         ("config", "config")):
            if kw in low:
                return role
        return "component"
