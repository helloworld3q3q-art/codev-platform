"""chroma 文档库支持 markdown 之外的文本类型(html 剥标签 / txt / rule 原样)。

文档库不写死 .md: doc_patterns(.claude/index.json)决定文件集, chunk_text 对无标题文本优雅降级
(段落/字符切分), html 经 _doc_text 剥标签。本测试钉死类型无关 + html 净化。
"""
from __future__ import annotations

import pytest

from codev_platform.chroma.chunking import chunk_text


@pytest.fixture
def indexer():
    """避免在 autouse 宿主配置隔离前冻结 indexer 的路径常量。"""
    from codev_platform.chroma import indexer as module

    return module


def test_strip_html_removes_tags_scripts_styles(indexer):
    html = ("<html><head><style>h1{color:red}</style></head>"
            "<body><h1>规则标题</h1><p>正文 &amp; 内容</p>"
            "<script>evil()</script><!-- 注释 --></body></html>")
    out = indexer._strip_html(html)
    assert "规则标题" in out and "正文 & 内容" in out  # 文本 + 实体解码
    assert "<" not in out and ">" not in out            # 无标签
    assert "evil" not in out and "color:red" not in out  # script/style 整块去掉
    assert "注释" not in out                             # 注释去掉


def test_doc_text_dispatch_by_extension(indexer):
    raw_rule = "R101 获取项目规则\n\n规则正文,纯文本无标签。"
    assert indexer._doc_text("ideas-private/rules/R101.rule", raw_rule) == raw_rule  # rule 原样
    assert indexer._doc_text("docs/x.txt", "plain text") == "plain text"            # txt 原样
    assert indexer._doc_text("docs/x.md", "# md\ncontent") == "# md\ncontent"        # md 原样
    assert "<" not in indexer._doc_text("public/page.html", "<p>hi</p>")            # html 剥标签


def test_chunk_text_handles_non_markdown_plaintext():
    # 无 # 标题的纯文本(.rule/.txt)不会被跳过, 走段落/字符切分
    txt = "第一段规则内容。\n\n第二段规则内容,描述获取项目规则的流程。"
    chunks = chunk_text(txt)
    assert chunks and all(c.strip() for c in chunks)
    assert "第一段规则内容" in "".join(chunks)
