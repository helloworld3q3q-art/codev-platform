"""代码向量索引 (Phase 6 vector lane) —— 对 codegraph 节点做语义嵌入, 支持「按行为描述找代码」。

**补的盲区**: codegraph / graph 两 lane 走符号名 FTS / 邻域子串, 答不了「下载完数据后写库的那段
逻辑在哪」这类**按行为/语义**的查询(查询里没有精确符号名)。本模块把 codegraph 已抽取的每个
节点(函数 / 类 / 方法 ...)的 name + qualifiedName + signature + docstring 嵌入向量, query 时按
语义相似度召回 —— 这是专家点名的唯一检索盲区。

**ref 复用 codegraph node id**: 向量 lane 与 codegraph lane **同一 ref 空间**, 融合(RRF)时同一对象
被两 lane 命中分数叠加, 自然增强; 无需另立 chunk 体系(chunk = 一个符号节点)。

接入铁律(对齐 agent-provider-architecture + agent memory 向量同源):
- 嵌入复用平台 `Embedder`(`agent.embed.registry.build_embedder`, 默认 qwen-local/cpu), 与 agent
  memory 向量同源、同接口; **缺依赖 / 模型 → None**, 调用侧据此 fail-soft(其它 lane 仍出结果)。
- collection `<pid>__code_vec`, 与 platform_docs(文档)/ agent_memory(记忆)隔离。
- **两段职责分离**: build_code_vector_index = 写批作业(跑在 WSL, 需嵌入模型, 重型不自动跑);
  query_code_vectors = recall service 查询侧薄壳; _parse_query_result = 纯函数(脱 IO 可单测)。

构建(在 WSL, 嵌入模型可用处跑):
    python -m codev_platform.recall.code_vector_store --project <project_id>
"""
from __future__ import annotations

import hashlib
import json
import logging

from codev_platform.core.paths import chroma_collection_name, chroma_dir
from codev_platform.core.index_handoff import (
    begin_build, commit_build, gc_builds, new_build_id, resolve_current,
)

logger = logging.getLogger(__name__)

# code_vec 库大(19万节点级); full handoff 切换瞬间已并存 2 份库, gc keep=1 只保 current 压磁盘峰值。
# (reader 是 per-query 短连接, 非常驻 daemon, 故 keep=1 比 docs 的 keep=2 安全 —— 撞读窗口仅单次查询。)
_CODE_VEC_KEEP = 1

_MANIFEST_NAME = ".manifest.json"        # id -> text sha1, 增量重建用(在 per-project persist 目录内)
_MANIFEST_META_NAME = ".manifest.meta.json"   # build 参数指纹 {enrich: bool}(与 manifest 分离, 不进 _diff)
_QUERY_CLIENTS: dict = {}                # path -> PersistentClient 进程内单例(chromadb 本就 per-path 单例, 显式化避免每查重 attach)

# 节点文本拼接字段(语义意义从强到弱); file 只入 metadata 不入嵌入文本(路径噪声)。
_TEXT_FIELDS = ("name", "qualifiedName", "signature", "docstring")

# 索引排除的低价值 kind(2026-06-11 综合验证发现): import=纯模块路径无语义体 / file=名即路径
# (与 file metadata 冗余)/ variable=多为局部·模块杂项。实测占两项目近半节点且污染语义召回
# (query「load config」召回的全是 import 节点而非 load_config 函数)。**blocklist 非 allowlist**:
# 跨语言 kind 词汇不同, 排除已证噪声的 3 类即可, 不漏 function/method/class/field/route 等有用 kind。
# 这是**默认值**; 各项目可经 config `recall.code_vec.skip_kinds` 覆盖(如 Java 重仓项目想再排
# field), 默认不变 → 平台与现存项目行为零改动。详见 _resolve_skip_kinds。
_DEFAULT_SKIP_KINDS = frozenset({"import", "file", "variable"})


def _resolve_skip_kinds(cfg: dict) -> frozenset:
    """解析索引排除 kind: config 未设 → 默认 3 类(import/file/variable); 设了 → 按 config(可为
    空 list = 不排除任何 kind = 全量嵌)。数据进 config 不写死, 单项目可调而不动平台默认与他项目。"""
    from codev_platform.core.config import get as _get
    raw = _get(cfg, "recall.code_vec.skip_kinds", None)
    if raw is None:
        return _DEFAULT_SKIP_KINDS
    if isinstance(raw, (list, tuple, set)):
        return frozenset(str(k).strip().lower() for k in raw if str(k).strip())
    return _DEFAULT_SKIP_KINDS


def _existing_chroma_healthy(persist) -> bool:
    """增量续跑前探活既有 code_vec 库。

    被 SIGKILL(如 worker 超时 timeout)中断在 flush 中途的 chroma 库会留**半写坏的 sqlite/
    segment**; 再往上 upsert 会触发 compaction 522 → 'database disk image is malformed' 把库彻底
    搞崩(2026-06-12 ideas-v2 实证)。续跑前读侧 quick_check 探一下, 坏了让调用方退全量 rmtree 拿
    干净库 —— 自愈而非接脏库越写越烂。读侧独立连接即开即关, 不引入 chromadb client 缓存副作用。
    无库文件(首建)视为健康(交全量逻辑处理)。"""
    db = persist / "chroma.sqlite3"
    if not db.exists():
        return True
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
            return bool(row) and str(row[0]).lower() == "ok"
        finally:
            con.close()
    except Exception:  # noqa: BLE001 — 打不开/读不出 = 已损坏 → 退全量
        return False


def code_vec_collection_name(project_id: str) -> str:
    """该项目的代码向量 collection 名(与 platform_docs / agent_memory 隔离)。"""
    return chroma_collection_name(project_id, "code_vec")


# code_vec 走**每项目独立 persist 目录**(chroma_dir()/code_vec/<pid>): 每个 sqlite 库只含该项目
# 单一 collection → 绕开 chromadb 1.5.9 多 flush compaction bug(该 bug 仅在「库里已存在别的
# collection」时多次 upsert 才触发; 单 collection 库多次 flush 永远安全, 见 incident 2026-06-05)。
# 故节点数 > chromadb 单次 upsert 硬上限(5461)时可安全分批写; 且项目间重建互不影响。
_CODE_VEC_SUBDIR = "code_vec"
_UPSERT_BATCH = 5000   # < chromadb 单次 upsert 硬上限 5461


def _code_vec_persist_dir(project_id: str):
    """该项目代码向量库的独立 persist 目录(单 collection 隔离)。"""
    return chroma_dir() / _CODE_VEC_SUBDIR / project_id


# 各字段长度上界: 防某些 parser 把整段方法体 / 巨型块注释塞进 signature / docstring(实测 ideas-v2
# 有 docstring 达 17KB 的 Java 方法)。base 不限 → 撑爆 + **每个滑窗都重复带一份** → 单 chunk 24KB:
# 既慢(成批长序列 encode 超 daemon 120s 上限)又稀释向量语义(检索质量差)。name/qualifiedName 短不限。
_BASE_FIELD_MAX = {"signature": 600, "docstring": 1200}


def build_text(node: dict) -> str:
    """codegraph 节点 → 基础嵌入文本(name + qualifiedName + signature + docstring, 跳空字段)。
    signature / docstring 按 _BASE_FIELD_MAX 截断(防超长字段撑爆每个 chunk, 见上)。"""
    parts: list[str] = []
    for f in _TEXT_FIELDS:
        v = node.get(f)
        if not v:
            continue
        s = str(v)
        cap = _BASE_FIELD_MAX.get(f)
        parts.append(s[:cap] if cap and len(s) > cap else s)
    return "\n".join(parts)


_SNIPPET_MAX_CHARS = 1500   # 源码片段截断(够含 docstring + 函数体, 不撑爆嵌入)


def _resolve_repo(project_id: str):
    """从 config.projects.<pid>.repo_path 解析仓 checkout 路径(读源码用)。缺/不存在 → None。"""
    from pathlib import Path

    from codev_platform.core.config import get as _get
    from codev_platform.core.config import load_config
    rp = _get(load_config(), f"projects.{project_id}.repo_path")
    if not rp:
        return None
    p = Path(rp).expanduser()
    return p if p.exists() else None


def _source_snippet(repo, node: dict, max_chars: int = _SNIPPET_MAX_CHARS) -> str:
    """读节点源码片段(start_line..end_line, 截断)—— 含 docstring + 函数体, 补 codegraph 未抽取的
    语义(实测 docstring 覆盖仅 ~7-11%)。repo/文件/行号缺或读失败 → ''(降级, 不崩)。"""
    if repo is None:
        return ""
    fp, s, e = node.get("filePath"), node.get("startLine"), node.get("endLine")
    if not fp or not s:
        return ""
    from pathlib import Path
    try:
        root = Path(repo).resolve()
        p = (root / fp).resolve()
        # 路径穿越防御: filePath 含 '..' / 绝对路径会逃出 repo, resolve 后必须仍在 repo 内才读。
        if not p.is_relative_to(root):
            logger.warning("[code_vec] 跳过越界 filePath(疑似路径穿越): %r", fp)
            return ""
        if not p.exists():
            return ""
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(lines[max(0, int(s) - 1):int(e or s)])[:max_chars]
    except Exception:  # noqa: BLE001 — 读源码失败仅降级
        return ""


def _embed_text(node: dict, repo) -> str:
    """[legacy, 单块] 嵌入文本 = 基础 + 截断源码片段。build 路径已改 _node_chunks(kind 感知+滑窗);
    本函数保留供单测 / 简单调用。"""
    base = build_text(node)
    snip = _source_snippet(repo, node)
    return f"{base}\n{snip}" if snip else base


# ---------- 切割策略: kind 感知 + 长方法滑窗 (替代固定 1500 字符硬截断) ----------
# 旧做法对每节点 build_text + 源码片段[:1500] 一刀切: 6.1% 节点被截(含 7492 个 method 切到一半、
# 巨型 god-class 757K 字符只嵌 0.2%)。改成:
#   - container(class/interface...): 只嵌**头部摘要**(类声明+字段+docstring), body 由其成员方法
#     节点各自覆盖, 不该把整个 body 塞进类向量(god-class 嵌也白嵌)。
#   - body(method/function...): 嵌**完整体**; 超 budget 则按**行边界滑窗**切多块, 尾部逻辑不丢。
#   - 单块用裸 node id(与 codegraph 同 ref 空间, RRF 叠分); 多块加 #k 后缀, 召回时去重回节点。
_CONTAINER_KINDS = frozenset({"class", "interface", "enum", "struct", "trait", "module", "namespace"})
_BODY_KINDS = frozenset({"method", "function", "constructor", "component"})
_CHUNK_BODY_CHARS = 3500     # 单块源码片段预算(method 体 / 默认); base(名/签名/docstring)另算
_CHUNK_OVERLAP_LINES = 8     # 滑窗重叠行数(跨窗的逻辑不被切断丢失)
_CLASS_HEAD_CHARS = 1800     # container 头部摘要预算(body 由成员节点覆盖)
_MAX_CHUNKS_PER_NODE = 12    # 单节点最多切几块(防超长 god-method 切出几百块; 12*3500≈4.2万字符封顶)


def _node_source_lines(repo, node: dict) -> list[str]:
    """节点源码行列表 (start_line..end_line), 路径穿越防御; repo/文件/行号缺或失败 → []。"""
    if repo is None:
        return []
    fp, s, e = node.get("filePath"), node.get("startLine"), node.get("endLine")
    if not fp or not s:
        return []
    from pathlib import Path
    try:
        root = Path(repo).resolve()
        p = (root / fp).resolve()
        if not p.is_relative_to(root):   # filePath 含 '..'/绝对路径逃出 repo → 拒读
            logger.warning("[code_vec] 跳过越界 filePath(疑似路径穿越): %r", fp)
            return []
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        return lines[max(0, int(s) - 1):int(e or s)]
    except Exception:  # noqa: BLE001 — 读源码失败仅降级
        return []


def _head_at_line_boundary(lines: list[str], budget: int) -> str:
    """取前若干**完整行**直到累计长度超 budget(不切到行中间)。至少收 1 行。"""
    out: list[str] = []
    n = 0
    for ln in lines:
        if out and n + len(ln) + 1 > budget:
            break
        out.append(ln)
        n += len(ln) + 1
    return "\n".join(out)


def _window_lines(lines: list[str], budget: int, overlap: int) -> list[str]:
    """按行边界滑窗切分: 每窗累计 ≤ budget 字符, 相邻窗重叠 overlap 行(跨窗逻辑不丢)。"""
    wins: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        cur: list[str] = []
        clen, j = 0, i
        while j < n:
            ln = lines[j]
            if cur and clen + len(ln) + 1 > budget:
                break
            if not cur and len(ln) + 1 > budget:   # 单行超 budget: 收该行(截断)避免空窗死循环
                cur.append(ln[:budget])
                j += 1
                break
            cur.append(ln)
            clen += len(ln) + 1
            j += 1
        wins.append("\n".join(cur))
        if j >= n:
            break
        i = max(j - overlap, i + 1)   # 重叠 + 必前进
    return wins


def _node_chunks(node: dict, repo) -> list[tuple[str, str]]:
    """节点 → [(chunk_id, text)]。kind 感知 + 长方法滑窗(见上注释)。空文本节点 → []。"""
    nid = str(node.get("id"))
    base = build_text(node)
    kind = node.get("kind") or ""
    lines = _node_source_lines(repo, node)

    if kind in _CONTAINER_KINDS:                       # 类等: 仅头部摘要 1 块
        head = _head_at_line_boundary(lines, _CLASS_HEAD_CHARS)
        text = f"{base}\n{head}" if head else base
        return [(nid, text)] if text.strip() else []

    if kind in _BODY_KINDS and lines and len("\n".join(lines)) > _CHUNK_BODY_CHARS:
        wins = _window_lines(lines, _CHUNK_BODY_CHARS, _CHUNK_OVERLAP_LINES)[:_MAX_CHUNKS_PER_NODE]
        out = [(f"{nid}#{k}", f"{base}\n{w}") for k, w in enumerate(wins) if (base + w).strip()]
        if out:
            return out
        return [(nid, base)] if base.strip() else []

    # 默认: 小 method / field / function / 其它 → 单块(头部片段, 绝大多数本就 < budget 不截)
    head = _head_at_line_boundary(lines, _CHUNK_BODY_CHARS)
    text = f"{base}\n{head}" if head else base
    return [(nid, text)] if text.strip() else []


def _node_id_of(chunk_id: str) -> str:
    """sub-chunk id → 裸 node id(剥 '#k' 后缀)。无后缀原样返回。"""
    return chunk_id.split("#", 1)[0]


def _node_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _diff_manifest(old: dict, new: dict) -> tuple[list[str], list[str]]:
    """增量 diff(纯函数): old/new = id→hash → (变更或新增 id 列表, 已删除 id 列表)。"""
    changed = [nid for nid, h in new.items() if old.get(nid) != h]
    deleted = [nid for nid in old if nid not in new]
    return changed, deleted


def _read_enrich_mode(meta_path) -> bool | None:
    """读上次 build 的源码富化模式(enrich bool); 缺/坏 → None(视为未知, 触发一次全量)。"""
    try:
        if not meta_path.exists():
            return None
        return bool(json.loads(meta_path.read_text(encoding="utf-8")).get("enrich"))
    except Exception:  # noqa: BLE001
        return None


def _get_query_client(persist_path: str):
    """query 侧 PersistentClient 进程内单例(per path)。chromadb 本就 per-path 单例, 显式缓存避免
    每次查询重建 client 反复 attach segment。"""
    import chromadb
    c = _QUERY_CLIENTS.get(persist_path)
    if c is None:
        c = chromadb.PersistentClient(path=persist_path)
        _QUERY_CLIENTS[persist_path] = c
    return c


def _parse_query_result(res: dict) -> tuple[list[str], dict]:
    """chroma query 结果 → (按相似度降序的 **node** id 列表, ref → {name,kind,file} 富化)。纯函数。

    长方法滑窗 → 同一节点可能多个 sub-chunk 命中: 按 meta.node(回退剥 '#k' 后缀)**去重回节点**,
    只留最高分那块。返回的恒是 codegraph node id(与 codegraph lane 同 ref 空间, RRF 可叠分)。"""
    ids_outer = res.get("ids") or [[]]
    metas_outer = res.get("metadatas") or [[]]
    ids = list(ids_outer[0]) if ids_outer else []
    metas = metas_outer[0] if metas_outer else []
    ranked: list[str] = []
    details: dict = {}
    seen: set[str] = set()
    for i, cid in enumerate(ids):
        m = metas[i] if i < len(metas) and metas[i] else {}
        nid = m.get("node") or _node_id_of(cid)   # 优先 meta.node; 无则剥后缀(向后兼容旧库)
        if nid in seen:
            continue                                # 同节点的后续(更低分)sub-chunk 丢弃
        seen.add(nid)
        ranked.append(nid)
        details[nid] = {"name": m.get("name"), "kind": m.get("kind"), "file": m.get("file")}
    return ranked, details


def query_code_vectors(project_id: str, query: str, k: int) -> tuple[list[str], dict]:
    """语义相似度召回 codegraph 节点 → (ranked node ids, details)。

    collection 缺失 → 顶部廉价 fs 探活快速空返(见下); 嵌入模型不可用 → ([], {}) 优雅空返。
    **先 fs 探活 → 开 collection → 后建 embedder**: 无索引时既不碰 chromadb 也不加载嵌入模型。
    """
    if k <= 0:                 # chromadb 对 n_results<=0 抛 TypeError; 正常边界值直接空返
        return [], {}
    persist = resolve_current(_code_vec_persist_dir(project_id))   # handoff: 读**当前 build**(无 pointer 退 base)
    # 廉价 fs 探活: 无**成功构建标记** _MANIFEST_NAME(仅成功 build 写)= 该项目没建好 code_vec →
    # 快速空返, **不 import chromadb / 不建 client**(省首次 PersistentClient 冷启动 ~700ms-1s)。
    # 注意不能只看 chroma.sqlite3: 空库/半建会留 0-collection 的 chroma.sqlite3(实测 codev-platform
    # 即此态: 库文件在、collections 空、无 manifest), 仍会触发冷启动 + get_collection NotFoundError。
    # 用我们自己的 manifest(非 chromadb 内部 schema)做标记, 健壮。有库的项目走原路, 行为不变。
    if not (persist / _MANIFEST_NAME).is_file():
        return [], {}
    client = _get_query_client(str(persist))   # 进程内单例(per path)
    col = client.get_collection(code_vec_collection_name(project_id))  # 缺 → 抛, 调用侧 fail-soft
    from codev_platform.agent.embed.registry import build_embedder
    from codev_platform.core.config import load_config
    embedder = build_embedder(load_config())
    if embedder is None:
        logger.warning("[code_vec] embedder 不可用(memory.embed.backend), 向量 lane 退化")
        return [], {}
    qv = embedder.encode(query)
    # 过取: 长方法切多块, top-k chunk 去重回节点后可能不足 k 个不同节点 → 多取再裁。
    over = min(max(k * 3, k), 200)
    res = col.query(query_embeddings=[qv], n_results=over, include=["metadatas"])
    ranked, details = _parse_query_result(res)
    ranked = ranked[:k]
    return ranked, {nid: details[nid] for nid in ranked}


class CodeVecLockBusy(RuntimeError):
    """另一个 code_vec 重建正占用同一 persist 目录(写侧互斥)。调用侧映射 rc=2 让 worker 重试。"""


def build_code_vector_index(project_id: str, *, incremental: bool = False) -> int:
    """构建/刷新代码向量索引: 校验 pid → 取写侧锁 → 委托 _build_locked。返回本次 embed 节点数。

    写侧锁(per-project persist 目录, 复用 indexer 同款 .reindex.lock): 防两个 code_vec build
    进程(如手动 reindex --force 撞 worker 增量)同时 rmtree/upsert/写 manifest 同一目录。
    锁忙 → CodeVecLockBusy(调用侧映射 rc=2 让 worker 重试, 不丢)。
    """
    from codev_platform.chroma._reindex_lock import release_reindex_lock, try_acquire_reindex_lock
    from codev_platform.core.project_id import validate as _validate_pid

    # 入口校验(纵深): rmtree(_code_vec_persist_dir(pid)) 用 pid 拼路径, 挡 '..'/分隔符删错目录
    # (正常 enqueue/CLI 入口已各自 validate, 此处兜底)。
    project_id = _validate_pid(project_id)
    persist = _code_vec_persist_dir(project_id)
    lock = try_acquire_reindex_lock(persist)
    if lock is None:
        raise CodeVecLockBusy(f"另一个 code_vec 重建正在跑, 跳过: {persist}")
    try:
        return _build_locked(project_id, persist, incremental=incremental)
    finally:
        release_reindex_lock(lock)


def _build_locked(project_id: str, persist, *, incremental: bool) -> int:
    """实际构建(已持写侧锁)。

    - incremental=False(或无 manifest / manifest 坏 / 富化模式翻转)→ **全量**: rmtree 物理清空
      (truly fresh sqlite)重灌。不用 delete_collection(残留旧 hnsw segment, 多批 upsert 撞 522)。
    - incremental=True 且 manifest 有效 → **增量**: 只重嵌变更/新增 + 删已不存在(小 upsert 安全便宜)。
    嵌入模型不可用直接抛(构建语境必须有模型, 不静默产空库)。
    """
    from codev_platform.agent.embed.registry import build_code_vec_embedder
    from codev_platform.core.config import load_config
    from codev_platform.web.integrations.codegraph_client import CodegraphClient

    # 索引侧用专用 embedder(默认本机 qwen-local + GPU 直跑), 不经共享 daemon /embed —— 大批量
    # 打 daemon 会长占其串行 GPU 信号量甚至死锁(连带打挂在线 search_docs)。build 与服务解耦。
    _cfg = load_config()
    skip_kinds = _resolve_skip_kinds(_cfg)   # 默认 import/file/variable; config 可覆盖, 不写死
    embedder = build_code_vec_embedder(_cfg)
    if embedder is None:
        raise RuntimeError(
            "embedder 不可用: 装 sentence-transformers + 配 models.embed_path(qwen-local), "
            "或设 recall.code_vec.embed_backend=remote 接 chroma daemon /embed。")

    import time

    import chromadb

    from codev_platform.chroma import ensure_wal

    base = persist                          # handoff base(.reindex.lock 落这, 锁仍挂 base 互斥整项目)
    current_dir = resolve_current(base)     # 读旧 manifest/meta + 探活 都看**当前 build**(无 pointer 退 base)
    cur_manifest_path = current_dir / _MANIFEST_NAME
    cur_meta_path = current_dir / _MANIFEST_META_NAME
    repo = _resolve_repo(project_id)   # 读源码片段补语义; None → 退基础文本(降级不崩)
    enrich_now = bool(repo)
    # 定 full(读 manifest/meta 从当前 build): 无 manifest / 损坏 / 富化翻转 / 探活坏 都退全量。
    # handoff: full **不再 rmtree 库**, 而是 begin_build 到干净 side(下面), 旧 build 留给 reader, commit 后 gc。
    full = (not incremental) or (not cur_manifest_path.exists())
    old_manifest: dict = {}
    if not full:
        try:
            old_manifest = json.loads(cur_manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — manifest 损坏 → 转全量(新 side), 不在旧 segment 上重灌
            logger.warning("[code_vec] manifest 损坏(%s), 转全量重建", exc)
            full = True
    # R3: 源码富化模式(repo 是否可解析)是 build 参数, 非节点内容。模式翻转(on<->off)会让每个
    # _embed_text 变化 → 当增量会把全部节点误标 changed + 谎报 incremental + 文本降级。检测翻转显式
    # 转全量(诚实记录), _diff_manifest 仍是纯 id->hash 不被污染(指纹存独立 meta 文件)。
    if not full and incremental:
        old_enrich = _read_enrich_mode(cur_meta_path)
        if old_enrich is not None and old_enrich != enrich_now:
            logger.warning("[code_vec] %s: 源码富化模式 %s->%s(repo 解析翻转), 强制全量重建"
                           "(否则会误报 incremental 且检索文本降级)", project_id, old_enrich, enrich_now)
            full = True
    # R5(2026-06-12): 增量续跑前探活当前 build —— 被 SIGKILL(worker 超时)中断的库 sqlite/segment 半
    # 写坏, 再 upsert 会触发 compaction 522/malformed 彻底崩。探到坏即退全量(新 side)拿干净库自愈。
    if not full and incremental and not _existing_chroma_healthy(current_dir):
        logger.warning("[code_vec] %s: 当前 build 探活失败(疑似被中断写坏), 转全量重建", project_id)
        full = True
    # ---- 唯一一处决定 build_dir(full 判定全部完成后)----
    if full:
        gc_builds(base, keep=_CODE_VEC_KEEP)            # 建新 side 前清旧(峰值压到 2 份库)
        bid = new_build_id(None, str(int(time.time())))
        build_dir = begin_build(base, bid)              # full → 干净 side(替代旧 rmtree(persist))
    else:
        bid = None
        build_dir = resolve_current(base)               # 增量 → 当前 build 原地改, 不 commit
    build_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = build_dir / _MANIFEST_NAME          # manifest/meta 跟 build dir 走
    meta_path = build_dir / _MANIFEST_META_NAME
    client = chromadb.PersistentClient(path=str(build_dir))
    ensure_wal(build_dir)
    name = code_vec_collection_name(project_id)
    col = client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})

    # 枚举节点 → 收 text/meta + 算新 manifest(跳过低价值 kind 与空文本)
    logger.info("[code_vec] %s: repo=%s (源码富化 %s)", project_id, repo, "on" if repo else "off")
    new_manifest: dict = {}
    text_by_id: dict[str, str] = {}
    meta_by_id: dict[str, dict] = {}
    with CodegraphClient(project_id) as cg:
        for node in cg.iter_nodes():
            nid = node.get("id")
            if node.get("kind") in skip_kinds:   # 低价值 kind 不入向量库(默认 import/file/variable)
                continue
            if not nid:
                continue
            nid = str(nid)
            # kind 感知切割: 一个节点可能产出多块(长方法滑窗); chunk_id 含 '#k' 后缀。
            for chunk_id, text in _node_chunks(node, repo):
                if not text.strip():
                    continue
                new_manifest[chunk_id] = _node_hash(text)
                text_by_id[chunk_id] = text
                meta_by_id[chunk_id] = {
                    "name": node.get("name") or "",
                    "kind": node.get("kind") or "",
                    "file": node.get("filePath") or "",
                    "node": nid,   # 召回去重回节点 + RRF 对齐 codegraph ref 空间
                }

    changed, deleted = _diff_manifest(old_manifest, new_manifest)

    if deleted:
        try:
            col.delete(ids=deleted)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[code_vec] 删除 %d 旧节点失败(忽略): %s", len(deleted), exc)

    # 增量 checkpoint manifest: 从旧 manifest 去掉已删, 每 upsert 一批就把这批已嵌入节点追加进去
    # 立即落盘。大项目 (ideas-v2 ~19万节点, GPU 算力约 1h) 即便中途超时/重启, 已完成进度不丢,
    # 下次只补剩余节点 (resume), 不再从零重嵌。
    _deleted_set = set(deleted)
    persisted = {k: v for k, v in old_manifest.items() if k not in _deleted_set}

    # 分批 embed + upsert 变更节点(每批 < 5461 硬上限; 单 collection 库多批 flush 安全)
    for i in range(0, len(changed), _UPSERT_BATCH):
        chunk = changed[i:i + _UPSERT_BATCH]
        # 批量 embed: remote adapter 一次 /embed 带一子批 (省掉每节点一次 HTTP 往返)。
        embs = embedder.encode_batch([text_by_id[nid] for nid in chunk])
        col.upsert(ids=chunk, embeddings=embs,
                   documents=[text_by_id[nid] for nid in chunk],
                   metadatas=[meta_by_id[nid] for nid in chunk])
        for nid in chunk:
            persisted[nid] = new_manifest[nid]
        manifest_path.write_text(json.dumps(persisted, ensure_ascii=False), encoding="utf-8")  # checkpoint
        logger.info("[code_vec] upserted %d/%d (manifest checkpointed)",
                    min(i + _UPSERT_BATCH, len(changed)), len(changed))

    # 收尾: 写完整 manifest (= 当前全部节点; persisted 此时已等同, 这步是精确兜底)。
    manifest_path.write_text(json.dumps(new_manifest, ensure_ascii=False), encoding="utf-8")
    meta_path.write_text(json.dumps({"enrich": enrich_now}, ensure_ascii=False), encoding="utf-8")
    # 发布: full 才原子切 current → 新 build(reader query 经 resolve_current 读到)。增量原地无需切。
    if full and bid is not None:
        commit_build(base, bid)
    logger.info("[code_vec] %s: %s, 变更 %d / 删除 %d / 总 %d 节点 → %s",
                project_id, "full" if full else "incremental",
                len(changed), len(deleted), len(new_manifest), name)
    return len(changed)


def main() -> int:
    import argparse

    from codev_platform.core.project_id import ProjectIdError, resolve_local

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="构建代码向量索引 (Phase 6 vector lane)")
    parser.add_argument("--project", help="project_id(缺省从 cwd .claude/project.json 解析)")
    parser.add_argument("--incremental", action="store_true",
                        help="增量(只重嵌变更节点; 无 manifest 自动退全量)。缺省=全量重建")
    args = parser.parse_args()

    pid = args.project
    if not pid:
        try:
            pid = resolve_local()
        except ProjectIdError as exc:
            print(f"[code_vec] FATAL: 无法解析 project_id: {exc}", flush=True)
            return 1
    n = build_code_vector_index(pid, incremental=args.incremental)
    print(f"[code_vec] done: {n} nodes (re)embedded for {pid}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
