"""CPU 嵌入长度上限回归测试。"""
from __future__ import annotations

import pytest
import sys
from types import SimpleNamespace

from codev_platform.chroma.embedding_limits import (
    apply_embed_max_seq_length,
    resolve_embed_max_seq_length,
)


def test_cpu_without_override_defaults_to_512_tokens():
    assert resolve_embed_max_seq_length("cpu", None) == 512
    assert resolve_embed_max_seq_length("CPU", "") == 512


def test_gpu_without_override_keeps_model_limit():
    assert resolve_embed_max_seq_length("cuda", None) == 0


def test_explicit_limit_overrides_device_default():
    assert resolve_embed_max_seq_length("cpu", "768") == 768
    assert resolve_embed_max_seq_length("cuda", 1024) == 1024


@pytest.mark.parametrize("configured", [True, False, -1, "invalid"])
def test_invalid_explicit_limit_is_rejected(configured):
    """错误配置必须启动即失败，不能静默退回并制造不可复现索引。"""
    with pytest.raises((TypeError, ValueError)):
        resolve_embed_max_seq_length("cpu", configured)


def test_apply_embed_max_seq_length_only_lowers_model_limit():
    class _Model:
        max_seq_length = 32768

    model = _Model()
    apply_embed_max_seq_length(model, 512)

    assert model.max_seq_length == 512


def test_apply_embed_max_seq_length_does_not_expand_model_limit():
    class _Model:
        max_seq_length = 256

    model = _Model()
    apply_embed_max_seq_length(model, 512)

    assert model.max_seq_length == 256


def test_cuda_load_failure_fallback_cpu_applies_cpu_default(monkeypatch):
    """配置 CUDA 但实际降级 CPU 时，未显式覆盖的模型长度必须按最终设备收紧。"""
    from codev_platform.chroma import _models

    class _Model:
        prompts = {}

        def __init__(self):
            self.max_seq_length = 32768

        def get_embedding_dimension(self):
            return 1024

    model = _Model()

    def _sentence_transformer(_path, *, device):
        if device == "cuda":
            raise RuntimeError("CUDA out of memory")
        return model

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=_sentence_transformer),
    )
    monkeypatch.setattr(_models, "_model", None)
    monkeypatch.setattr(_models, "_global_init_error", None)
    monkeypatch.setattr(_models, "EMBED_DEVICE", "cuda")
    monkeypatch.setattr(_models, "EMBED_MAX_SEQ_LENGTH", 0)
    monkeypatch.setattr(_models, "EMBED_MAX_SEQ_LENGTH_OVERRIDE", None, raising=False)
    monkeypatch.setattr(_models, "_load_with_retry", lambda _name, loader: loader())
    monkeypatch.setattr(_models, "_is_gpu_error", lambda _exc: True)
    monkeypatch.setattr(_models, "_flog", lambda _message: None)

    assert _models._ensure_model() is model
    assert model.max_seq_length == 512
