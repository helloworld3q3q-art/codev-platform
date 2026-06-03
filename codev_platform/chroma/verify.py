"""一次性验证脚本：直接打 Chroma 测语义检索质量

跑法：
    tools/chroma/.venv/Scripts/python.exe tools/chroma/verify_search.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

# 强制 stdout UTF-8，否则 Windows cmd 默认 GBK 撞 emoji / 部分中文报错
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import chromadb
from sentence_transformers import SentenceTransformer

# 数据路径 + 模型走 codev-platform config (跨项目共享 chroma DB / 模型路径)
from codev_platform.core.config import load_config, env_or_config  # noqa: E402
from codev_platform.core.paths import chroma_dir  # noqa: E402

DATA_DIR = chroma_dir()
_cfg = load_config()
MODEL = str(Path(env_or_config("PLATFORM_EMBED_MODEL_PATH", _cfg, "models.embed_path")).expanduser().resolve())
DEVICE = env_or_config("PLATFORM_EMBED_DEVICE", _cfg, "models.embed_device", "cuda")

QUERIES = [
    "影子规则数据隔离的写侧防御",
    "Python 和 Java SQL 跨层公式不一致",
    "policy_version 在 ON CONFLICT 应该用 COALESCE 哪个顺序",
    "PIT 红线下哪些日期可以重跑哪些不能",
    "新增前端页面需要同步写 sys_menu 迁移",
    "AI 写代码项目里 grep 找不到语义相似的实现",
]


def main() -> int:
    client = chromadb.PersistentClient(path=str(DATA_DIR))
    model = SentenceTransformer(MODEL, device=DEVICE)
    prompts = getattr(model, "prompts", None) or {}
    use_query_prompt = "query" in prompts and bool(prompts.get("query"))
    col = client.get_collection("platform_docs")
    get_dim = model.get_embedding_dimension if hasattr(model, "get_embedding_dimension") else model.get_sentence_embedding_dimension
    print(
        f"[OK] collection loaded, total chunks = {col.count()}, device = {DEVICE}, "
        f"model = {Path(MODEL).name}, dim = {get_dim()}, "
        f"max_seq = {getattr(model, 'max_seq_length', '?')}, query_prompt = {use_query_prompt}\n"
    )

    for q in QUERIES:
        print("=" * 80)
        print(f"Q: {q}")
        print("-" * 80)
        kwargs = {"normalize_embeddings": True, "convert_to_numpy": True}
        if use_query_prompt:
            kwargs["prompt_name"] = "query"
        qvec = model.encode([q], **kwargs)[0].tolist()
        res = col.query(query_embeddings=[qvec], n_results=3)
        ids = res["ids"][0]
        docs = res["documents"][0]
        metas = res["metadatas"][0]
        dists = res["distances"][0]
        for i, _id in enumerate(ids):
            meta = metas[i] or {}
            # 去掉所有非 BMP 字符（emoji 等），避免 Windows cmd GBK 输出炸
            raw = docs[i][:160].replace("\n", " ")
            snippet = "".join(c for c in raw if ord(c) < 0x10000)
            print(f"  #{i+1} dist={dists[i]:.3f} cat={meta.get('category'):<10} {meta.get('file')}")
            print(f"      {snippet}...")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
