"""chroma daemon /embed /rerank 请求体校验(从 handler 闭包提到模块级, 清审计 NIT1)。

纯函数: 不碰模型/GPU/starlette, 直接断言 400 触发条件(非 list / 空 / 非 str / 缺字段)。
"""
from __future__ import annotations

from codev_platform.chroma.server import validate_embed_body, validate_rerank_body


# ---- /embed ----

def test_embed_valid_texts_list():
    assert validate_embed_body({"texts": ["a", "b"]}) == (["a", "b"], None)


def test_embed_single_text_wrapped():
    assert validate_embed_body({"text": "hi"}) == (["hi"], None)


def test_embed_non_list_rejected():
    assert validate_embed_body({"texts": "a"})[0] is None
    assert validate_embed_body({"texts": 123})[1] is not None


def test_embed_empty_rejected():
    assert validate_embed_body({"texts": []})[0] is None
    assert validate_embed_body({})[0] is None


def test_embed_non_str_element_rejected():
    assert validate_embed_body({"texts": ["a", 1]})[0] is None


def test_embed_non_dict_rejected():
    assert validate_embed_body("nope") == (None, "invalid json")


# ---- /rerank ----

def test_rerank_valid():
    assert validate_rerank_body({"query": "q", "docs": ["a", "b"]}) == (("q", ["a", "b"]), None)


def test_rerank_missing_query_rejected():
    assert validate_rerank_body({"docs": ["a"]})[0] is None


def test_rerank_non_str_query_rejected():
    assert validate_rerank_body({"query": 5, "docs": ["a"]})[0] is None


def test_rerank_empty_or_non_list_docs_rejected():
    assert validate_rerank_body({"query": "q", "docs": []})[0] is None
    assert validate_rerank_body({"query": "q", "docs": "a"})[0] is None


def test_rerank_non_str_doc_rejected():
    assert validate_rerank_body({"query": "q", "docs": ["a", 2]})[0] is None


def test_rerank_non_dict_rejected():
    assert validate_rerank_body(None) == (None, "invalid json")
