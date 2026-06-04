"""前端模块依赖扫描 —— 接 dependency-cruiser(成熟工具)产前端组件依赖图。

为何接外部工具而非手写(2026-06-04 验证教训): 前端模块解析(`@/` alias / barrel re-export /
index 省略 / 扩展名)是出了名的坑, 手写正则做不对; codegraph 又只把 import 记到模块级、不解析
alias(实测公共组件 incoming 仅 contains, 答不了"改组件→影响哪些页面")。dependency-cruiser 读
tsconfig paths 解 alias, 输出已 resolve 的模块依赖图 —— 这是该 codegraph 盲区的正解, 不造轮子。

产出: frontend_component 节点(每个 src 模块, meta.is_page 标 pages/ 下的页面)+ renders 边
(A import B => A --renders--> B)。反向遍历 renders 到 is_page 节点 = "改这个组件影响哪些页面"。

🔑 关键配置(实测): 必须用 **--config 文件**设 `tsConfig.fileName` 才启用 tsconfig-paths resolver
解 `@/` alias(`--ts-config` CLI flag 不解, 实测 platform 仓依赖边 7 vs 524)。

fail-soft: 无 node/npx / depcruise 跑失败 / 无前端子目录 => 返回空, 绝不拖垮 ingest。
性能: spawn node + 首跑 npx 下载 dependency-cruiser(~分钟级), 故由独立 pass 调、非每插件必跑。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from codev_platform.graph.schema import EdgeKind, GraphEdge, GraphNode, NodeKind

from ._common import _has_file_with_suffix, _rel, logger

# depcruise config 模板: tsConfig 启用 tsconfig-paths 解 alias(见模块 docstring 🔑)。
# %s = 项目 tsconfig 文件名(相对前端根)。单引号 here-doc 风格, 不插值业务数据。
_DEPCRUISE_CONFIG_TMPL = """module.exports = {
  options: {
    doNotFollow: { path: "node_modules" },
    includeOnly: "^src",
    tsConfig: { fileName: %s },
    tsPreCompilationDeps: true,
    enhancedResolveOptions: { extensions: [".ts", ".tsx", ".js", ".jsx", ".vue", ".json", ".d.ts"] }
  }
};
"""
_DEPCRUISE_PKG = "dependency-cruiser@17"  # pin major(17 = 当前最新). ⚠️ 勿降 16: 实测 16.10.4
# 解析不全(platform 依赖边 254 vs 17.4.3 的 524, barrel/re-export 传递链断 → 反向"影响页面"全空)
_TIMEOUT_S = 300
_SKIP_PARTS = frozenset({"node_modules", ".umi", ".umi-production", ".git", "dist", "build"})


_PAGE_DIR_HINTS = ("/pages/", "/views/")
_NEXT_PAGE_FILES = ("page.tsx", "page.jsx", "page.ts", "page.js")


def _node_available() -> bool:
    return shutil.which("npx") is not None


def _is_page(source: str) -> bool:
    """页面判定(启发式): pages/(react/umi) / views/(vue) / Next app router 的 app/.../page.*。

    已知盲区(反向"受影响页面"会少算): umi `config/routes.ts` 显式注册的非常规目录(需读路由
    配置, 第一版未覆盖)。
    """
    if any(h in source for h in _PAGE_DIR_HINTS):
        return True
    return "/app/" in source and source.rsplit("/", 1)[-1] in _NEXT_PAGE_FILES


def _depcruise_base(front: Path) -> list[str] | None:
    """depcruise 命令前缀: 优先前端**预装**(node_modules/.bin, 快+离线), 否则 npx --yes 下载。

    无 node/npx 返回 None。生产应在前端仓 / 平台预装 dependency-cruiser, 避免每次 ingest 联网。
    """
    for binname in ("depcruise.cmd", "depcruise"):
        local = front / "node_modules" / ".bin" / binname
        if local.exists():
            return ["cmd", "/c", str(local)] if binname.endswith(".cmd") else [str(local)]
    npx = shutil.which("npx")
    if not npx:
        return None
    prefix = ["cmd", "/c", npx] if npx.lower().endswith((".cmd", ".bat")) else [npx]
    return [*prefix, "--yes", _DEPCRUISE_PKG]


def _src_fingerprint(front: Path) -> str:
    """src 下前端源文件 (rel, mtime_ns, size) 聚合 hash —— 快(不读内容), 文件增删改即变。"""
    import hashlib
    h = hashlib.sha256()
    src = front / "src"
    for f in sorted(src.rglob("*")):
        if f.suffix not in (".ts", ".tsx", ".js", ".jsx", ".vue") or _SKIP_PARTS & set(f.parts):
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        h.update(f"{f.relative_to(front)}:{st.st_mtime_ns}:{st.st_size}\n".encode())
    return h.hexdigest()


def _cache_file(project_id: str, front: Path) -> Path | None:
    """缓存 JSON(平台 data 下, per pid+front)。data 不可写 → None(退回每次全量, 不影响正确性)。"""
    try:
        import hashlib
        from codev_platform.core.paths import data_root
        key = hashlib.sha256(str(front).encode()).hexdigest()[:12]
        d = data_root() / "frontend_deps_cache"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{project_id}__{key}.json"
    except OSError:
        return None


def _frontend_roots(repo: Path) -> list[Path]:
    """找前端子目录: 含 tsconfig.json + src/ 且 src 下有 .tsx(React)或 .vue(Vue)组件。

    os.walk + 原地剪枝 node_modules/.git/dist 等 —— rglob 会**遍历进 node_modules**, 在
    monorepo(几万依赖文件)上每次 scan 巨慢(实测 platform ~22s/次), 剪枝后秒级。
    """
    import os
    roots: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_PARTS]  # 原地剪枝, 不下钻这些
        if "tsconfig.json" not in filenames:
            continue
        d = Path(dirpath)
        src = d / "src"
        if src.is_dir() and (_has_file_with_suffix(src, ".tsx")
                             or _has_file_with_suffix(src, ".vue")):
            roots.append(d)
    return roots


def _run_depcruise(front: Path, project_id: str) -> dict | None:
    """在前端根跑 dependency-cruiser, 回解析后的 JSON(modules);失败返回 None(fail-soft)。

    优先用前端预装的 depcruise(否则 npx --yes 下载)。**fingerprint 缓存**: src 文件 mtime/size
    没变 → 直接返回上次结果, 跳过整个 depcruise spawn(省每次 reindex ~分钟级; dependency-cruiser
    内置 --cache 实测不省 spawn, 故自管)。config 用临时文件(含 pid 防并发), 跑完即删。
    """
    base = _depcruise_base(front)
    if base is None:
        return None
    fp = _src_fingerprint(front)
    cf = _cache_file(project_id, front)
    if cf is not None and cf.exists():
        try:
            cached = json.loads(cf.read_text(encoding="utf-8"))
            if cached.get("fingerprint") == fp:
                return cached["data"]  # 命中: src 未变, 跳过整个 depcruise
        except (OSError, ValueError):
            pass  # 缓存损坏 → 重跑
    cfg = front / f".dependency-cruiser.codev.{os.getpid()}.cjs"
    try:
        cfg.write_text(_DEPCRUISE_CONFIG_TMPL % json.dumps("tsconfig.json"), encoding="utf-8")
        proc = subprocess.run(
            [*base, "--config", cfg.name, "--output-type", "json",
             "src/**/*.{ts,tsx,jsx,js,vue}"],
            cwd=str(front), capture_output=True, text=True, encoding="utf-8",
            timeout=_TIMEOUT_S, shell=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            logger.warning("[frontend_deps] depcruise rc=%s at %s", proc.returncode, front)
            return None
        data = json.loads(proc.stdout)
        if cf is not None:
            try:
                cf.write_text(json.dumps({"fingerprint": fp, "data": data}), encoding="utf-8")
            except OSError:
                pass  # 缓存写失败不影响结果
        return data
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        logger.warning("[frontend_deps] depcruise failed at %s: %r", front, exc)
        return None
    finally:
        cfg.unlink(missing_ok=True)


def scan_frontend_deps(
    repo: Path, project_id: str,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """扫前端组件依赖图: frontend_component 节点 + renders 边(A import B => A renders B)。

    fail-soft: 无 node/npx 或工具跑挂 => 返回 ([], [])。多前端根(monorepo)各扫各的并合并。
    """
    if not _node_available():
        logger.info("[frontend_deps] npx not found, skip frontend dep scan")
        return [], []

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    seen_node: set[str] = set()
    for front in _frontend_roots(repo):
        data = _run_depcruise(front, project_id)
        if not data:
            continue
        front_rel = _rel(front, repo)  # 前端根相对 repo(monorepo 下如 apps/stock-admin-web)
        mods = data.get("modules", [])
        # module.source 相对前端根; 拼 front_rel 成相对 repo 的稳定路径作 node 锚。
        src_set = {m.get("source") for m in mods}
        for m in mods:
            src = m.get("source")
            if not src:
                continue
            nid = _comp_id(project_id, front_rel, src)
            if nid not in seen_node:
                seen_node.add(nid)
                nodes.append(_comp_node(project_id, front_rel, src, nid))
            for dep in m.get("dependencies", []):
                tgt = dep.get("resolved")
                if not tgt or tgt not in src_set:
                    continue  # 只连 src 内部依赖(外部包 / 类型不建边)
                edges.append(GraphEdge(
                    source=nid,
                    target=_comp_id(project_id, front_rel, tgt),
                    kind=EdgeKind.IMPORTS.value,
                    meta={"via": "dependency-cruiser"},
                ))
    return nodes, edges


def _comp_id(project_id: str, front_rel: str, source: str) -> str:
    path = f"{front_rel}/{source}" if front_rel not in ("", ".") else source
    return f"{project_id}:frontend_component:{path}"


def _comp_node(project_id: str, front_rel: str, source: str, nid: str) -> GraphNode:
    path = f"{front_rel}/{source}" if front_rel not in ("", ".") else source
    # 组件名 = 文件名去扩展; index.* 用父目录名(barrel 约定, 同 scan_react_pages 命名 +
    # agent 直觉: 查 "PermissionButton" 而非 "PermissionButton.tsx", 一堆 index 也可辨识)。
    stem = source.rsplit("/", 1)[-1]
    base = stem.rsplit(".", 1)[0] if "." in stem else stem
    if base == "index":
        segs = source.rstrip("/").split("/")
        name = segs[-2] if len(segs) >= 2 else base
    else:
        name = base
    return GraphNode(
        id=nid,
        kind=NodeKind.FRONTEND_MODULE.value,
        name=name,
        project_id=project_id,
        file=path,
        language="typescript",
        meta={"is_page": _is_page(source)},
    )
