"""显卡参数配置化(2026-06-01): device / dtype / batch 不写死, 换显卡只改 config。

锁定 _torch_dtype 的解析 + auto 兼容旧行为(cuda→fp16 / 否则 fp32)。
reranker 的 .to(device) 与 batch 配置走运行期(需 GPU), 这里只钉纯逻辑。
"""
from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")  # 轻量环境无 torch 时跳过(不阻塞 CI)


def _dtype():
    # server 现已可在无 chromadb 时 import(audit #1);_torch_dtype 不依赖 chromadb。
    from codev_platform.chroma.server import _torch_dtype
    return _torch_dtype


def test_auto_cuda_is_fp16():
    assert _dtype()("auto", "cuda") is torch.float16
    assert _dtype()("auto", "cuda:1") is torch.float16  # 多卡 pin 也按 cuda 系


def test_auto_cpu_is_fp32():
    assert _dtype()("auto", "cpu") is torch.float32


def test_explicit_dtypes():
    d = _dtype()
    assert d("bfloat16", "cuda") is torch.bfloat16
    assert d("bf16", "cuda") is torch.bfloat16
    assert d("float16", "cpu") is torch.float16
    assert d("float32", "cuda") is torch.float32


def test_unknown_falls_back_fp32():
    assert _dtype()("weird", "cuda") is torch.float32
