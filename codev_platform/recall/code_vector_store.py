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

logger = logging.getLogger(__name__)

_MANIFEST_NAME = ".manifest.json"   # id -> text sha1, 增量重建用(在 per-project persist 目录内)

# 节点文本拼接字段(语义意义从强到弱); file 只入 metadata 不入嵌入文本(路径噪声)。
_TEXT_FIELDS = ("name", "qualifiedName", "signature", "docstring")

# 索引排除的低价值 kind(2026-06-11 综合验证发现): import=纯模块路径无语义体 / file=名即路径
# (与 file metadata 冗余)/ variable=多为局部·模块杂项。实测占两项目近半节点且污染语义召回
# (query「load config」召回的全是 import 节点而非 load_config 函数)。**blocklist 非 allowlist**:
# 跨语言 kind 词汇不同, 排除已证噪声的 3 类即可, 不漏 function/method/class/field/route 等有用 kind。
_SKIP_KINDS = frozenset({"import", "file", "variable"})


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


def build_text(node: dict) -> str:
    """codegraph 节点 → 基础嵌入文本(name + qualifiedName + signature + docstring, 跳空字段)。"""
    return "\n".join(str(node[f]) for f in _TEXT_FIELDS if node.get(f))


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
        p = Path(repo) / fp
        if not p.exists():
            return ""
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(lines[max(0, int(s) - 1):int(e or s)])[:max_chars]
    except Exception:  # noqa: BLE001 — 读源码失败仅降级
        return ""


def _embed_text(node: dict, repo) -> str:
    """索引侧嵌入文本 = 基础(名/签名/docstring)+ 源码片段(补语义)。无源码退基础。"""
    base = build_text(node)
    snip = _source_snippet(repo, node)
    return f"{base}\n{snip}" if snip else base


def _node_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _diff_manifest(old: dict, new: dict) -> tuple[list[str], list[str]]:
    """增量 diff(纯函数): old/new = id→hash → (变更或新增 id 列表, 已删除 id 列表)。"""
    changed = [nid for nid, h in new.items() if old.get(nid) != h]
    deleted = [nid for nid in old if nid not in new]
    return changed, deleted


def _parse_query_result(res: dict) -> tuple[list[str], dict]:
    """chroma query 结果 → (按相似度降序的 node id 列表, ref → {name,kind,file} 富化)。纯函数。"""
    ids_outer = res.get("ids") or [[]]
    metas_outer = res.get("metadatas") or [[]]
    ids = list(ids_outer[0]) if ids_outer else []
    metas = metas_outer[0] if metas_outer else []
    details: dict = {}
    for i, nid in enumerate(ids):
        m = metas[i] if i < len(metas) and metas[i] else {}
        details[nid] = {"name": m.get("name"), "kind": m.get("kind"), "file": m.get("file")}
    return ids, details


def query_code_vectors(project_id: str, query: str, k: int) -> tuple[list[str], dict]:
    """语义相似度召回 codegraph 节点 → (ranked node ids, details)。

    collection 缺失 → chromadb 抛(调用侧 fail-soft); 嵌入模型不可用 → ([], {}) 优雅空返。
    **先开 collection 后建 embedder**: 无索引时不白加载嵌入模型(省内存 + 单测脱模型)。
    """
    import chromadb

    client = chromadb.PersistentClient(path=str(_code_vec_persist_dir(project_id)))
    col = client.get_collection(code_vec_collection_name(project_id))  # 缺 → 抛, 调用侧 fail-soft
    from codev_platform.agent.embed.registry import build_embedder
    from codev_platform.core.config import load_config
    embedder = build_embedder(load_config())
    if embedder is None:
        logger.warning("[code_vec] embedder 不可用(memory.embed.backend), 向量 lane 退化")
        return [], {}
    qv = embedder.encode(query)
    res = col.query(query_embeddings=[qv], n_results=k, include=["metadatas"])
    return _parse_query_result(res)


def build_code_vector_index(project_id: str, *, incremental: bool = False) -> int:
    """构建/刷新该项目代码向量索引; 返回**本次 embed 的节点数**(增量=变更数, 全量=全部)。

    - `incremental=False`(或无 manifest)→ **全量**: 物理清空目录(truly fresh sqlite)重灌。
      不用 delete_collection —— 后者残留旧 hnsw segment, 多批 upsert compaction 撞残留报
      disk I/O (522)(incident 2026-06-05); 全新单 collection 库多批 flush 才永远安全。
    - `incremental=True` 且有 manifest → **增量**: 只对 text 变更/新增节点重嵌 + 删除已不存在
      节点。小 upsert 既便宜又安全(事故明确「增量小 upsert 是安全模式, 全量 bulk multi-flush
      才触发 522」), 适合接 reindex worker 随提交刷新。manifest(id→text sha1)存目录内。

    嵌入模型不可用直接抛(构建语境必须有模型, 不静默产空库)。
    """
    from codev_platform.agent.embed.registry import build_embedder
    from codev_platform.core.config import load_config
    from codev_platform.web.integrations.codegraph_client import CodegraphClient

    embedder = build_embedder(load_config())
    if embedder is None:
        raise RuntimeError(
            "embedder 不可用: 装 sentence-transformers + 配 models.embed_path(qwen-local), "
            "或设 memory.embed.backend=remote 接 chroma daemon /embed。")

    import shutil

    import chromadb

    from codev_platform.chroma import ensure_wal

    persist = _code_vec_persist_dir(project_id)
    manifest_path = persist / _MANIFEST_NAME
    full = (not incremental) or (not manifest_path.exists())   # 无 manifest 不可信增量 → 退全量
    if full:
        shutil.rmtree(persist, ignore_errors=True)
    persist.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist))
    ensure_wal(persist)
    name = code_vec_collection_name(project_id)
    col = client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})

    old_manifest: dict = {}
    if not full:
        try:
            old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — manifest 坏 → 当空(全量重嵌, 但不 rmtree)
            logger.warning("[code_vec] manifest 损坏(%s), 本次全量重嵌", exc)

    # 枚举节点 → 收 text/meta + 算新 manifest(跳过低价值 kind 与空文本)
    repo = _resolve_repo(project_id)   # 读源码片段补语义; None → 退基础文本(降级不崩)
    logger.info("[code_vec] %s: repo=%s (源码富化 %s)", project_id, repo, "on" if repo else "off")
    new_manifest: dict = {}
    text_by_id: dict[str, str] = {}
    meta_by_id: dict[str, dict] = {}
    with CodegraphClient(project_id) as cg:
        for node in cg.iter_nodes():
            nid = node.get("id")
            if node.get("kind") in _SKIP_KINDS:   # 低价值 kind 不入向量库(import/file/variable)
                continue
            text = _embed_text(node, repo)         # 名/签名/docstring + 源码片段
            if not nid or not text.strip():
                continue
            nid = str(nid)
            new_manifest[nid] = _node_hash(text)
            text_by_id[nid] = text
            meta_by_id[nid] = {
                "name": node.get("name") or "",
                "kind": node.get("kind") or "",
                "file": node.get("filePath") or "",
            }

    changed, deleted = _diff_manifest(old_manifest, new_manifest)

    if deleted:
        try:
            col.delete(ids=deleted)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[code_vec] 删除 %d 旧节点失败(忽略): %s", len(deleted), exc)

    # 分批 embed + upsert 变更节点(每批 < 5461 硬上限; 单 collection 库多批 flush 安全)
    for i in range(0, len(changed), _UPSERT_BATCH):
        chunk = changed[i:i + _UPSERT_BATCH]
        embs = [embedder.encode(text_by_id[nid]) for nid in chunk]
        col.upsert(ids=chunk, embeddings=embs,
                   documents=[text_by_id[nid] for nid in chunk],
                   metadatas=[meta_by_id[nid] for nid in chunk])
        logger.info("[code_vec] upserted %d/%d", min(i + _UPSERT_BATCH, len(changed)), len(changed))

    manifest_path.write_text(json.dumps(new_manifest, ensure_ascii=False), encoding="utf-8")
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
