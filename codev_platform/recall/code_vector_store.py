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

import logging

from codev_platform.core.paths import chroma_collection_name, chroma_dir

logger = logging.getLogger(__name__)

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
    """codegraph 节点 → 嵌入文本(name + qualifiedName + signature + docstring, 跳空字段)。"""
    return "\n".join(str(node[f]) for f in _TEXT_FIELDS if node.get(f))


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


def build_code_vector_index(project_id: str) -> int:
    """全量重建该项目代码向量索引(MVP: 删旧 collection 重灌); 返回索引节点数。

    枚举 codegraph 全节点 → 逐节点嵌入 name/sig/docstring → 单次 upsert(避开 chromadb 多 flush
    compaction bug, 见 docs/incidents/2026-06-05-chromadb-multiflush-compaction.md)。
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

    import chromadb
    from codev_platform.chroma import ensure_wal

    import shutil

    persist = _code_vec_persist_dir(project_id)
    # 全量重建 = **物理清空该项目目录**(truly fresh sqlite), 不用 delete_collection —— 后者残留
    # 旧 hnsw segment, 多批 upsert 时 compaction 撞残留报 disk I/O (522)(incident 2026-06-05)。
    # 全新单 collection 库多批 flush 才永远安全(实测 openclaw 10053 节点 3 批: delete_collection
    # 路径第 2 批必崩, rmtree 路径全过)。
    shutil.rmtree(persist, ignore_errors=True)
    persist.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist))
    ensure_wal(persist)
    name = code_vec_collection_name(project_id)
    col = client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})

    ids: list[str] = []
    embs: list[list[float]] = []
    docs: list[str] = []
    metas: list[dict] = []
    with CodegraphClient(project_id) as cg:
        for node in cg.iter_nodes():
            nid = node.get("id")
            if node.get("kind") in _SKIP_KINDS:   # 低价值 kind 不入向量库(import/file/variable)
                continue
            text = build_text(node)
            if not nid or not text.strip():
                continue
            ids.append(str(nid))
            embs.append(embedder.encode(text))
            docs.append(text)
            metas.append({
                "name": node.get("name") or "",
                "kind": node.get("kind") or "",
                "file": node.get("filePath") or "",
            })
            if len(ids) % 200 == 0:
                logger.info("[code_vec] embedded %d nodes ...", len(ids))

    # 分批 upsert(每批 < 5461 硬上限)。独立 persist 目录 = 单 collection 库, 多批 flush 安全。
    for i in range(0, len(ids), _UPSERT_BATCH):
        sl = slice(i, i + _UPSERT_BATCH)
        col.upsert(ids=ids[sl], embeddings=embs[sl], documents=docs[sl], metadatas=metas[sl])
        logger.info("[code_vec] upserted %d/%d", min(i + _UPSERT_BATCH, len(ids)), len(ids))
    logger.info("[code_vec] %s: 索引 %d 节点 → collection %s", project_id, len(ids), name)
    return len(ids)


def main() -> int:
    import argparse

    from codev_platform.core.project_id import ProjectIdError, resolve_local

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="构建代码向量索引 (Phase 6 vector lane)")
    parser.add_argument("--project", help="project_id(缺省从 cwd .claude/project.json 解析)")
    args = parser.parse_args()

    pid = args.project
    if not pid:
        try:
            pid = resolve_local()
        except ProjectIdError as exc:
            print(f"[code_vec] FATAL: 无法解析 project_id: {exc}", flush=True)
            return 1
    n = build_code_vector_index(pid)
    print(f"[code_vec] done: {n} nodes indexed for {pid}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
