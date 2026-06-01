"""chroma server 模型/reranker 载入有界重试 —— 纯逻辑单测, 不碰 GPU / chromadb。

覆盖: retry_delays 退避序列 (次数/上限/cap) + should_retry 瞬时判定 +
_load_with_retry fail-then-succeed / 耗尽 / 非瞬时立即放弃 (mock loader, 无真模型)。
"""
from __future__ import annotations

import pytest

from codev_platform.chroma import server as s


def test_retry_delays_default_3_attempts():
    # 3 次尝试 = 2 次 sleep, 指数 0.5 -> 1.0
    assert s.retry_delays(3, base=0.5, cap=2.0) == [0.5, 1.0]


def test_retry_delays_caps_growth():
    # 4 次尝试: 0.5, 1.0, 2.0(被 cap 截) — 第 4 项本应 4.0 但只有 3 个间隔
    assert s.retry_delays(4, base=0.5, cap=2.0) == [0.5, 1.0, 2.0]
    # cap 生效: base 大时立即被压到 cap
    assert s.retry_delays(3, base=5.0, cap=2.0) == [2.0, 2.0]


def test_retry_delays_no_retry_when_one_or_zero_attempts():
    assert s.retry_delays(1) == []
    assert s.retry_delays(0) == []


def test_retry_delays_total_is_bounded():
    delays = s.retry_delays(3, base=0.5, cap=2.0)
    assert sum(delays) <= 2.0  # 总退避有上限, 不长阻塞


def test_should_retry_transient_and_exhaustion():
    oom = RuntimeError("CUDA out of memory")
    plain = ValueError("model path missing")
    # 瞬时 + 未耗尽 → 重试
    assert s.should_retry(1, 3, oom) is True
    assert s.should_retry(2, 3, oom) is True
    # 到达上限 → 不重试
    assert s.should_retry(3, 3, oom) is False
    # 非瞬时 (路径错) → 即便没耗尽也不重试
    assert s.should_retry(1, 3, plain) is False


def test_load_with_retry_fail_then_succeed(monkeypatch):
    monkeypatch.setattr(s.time, "sleep", lambda *_: None)  # 不真睡
    s._stats["embedding"]["load_retries"] = 0
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("CUDA device busy")  # 瞬时, 第二次成功
        return "MODEL"

    assert s._load_with_retry("embedding", loader, max_attempts=3) == "MODEL"
    assert calls["n"] == 2
    assert s._stats["embedding"]["load_retries"] == 1  # 记了一次重试


def test_load_with_retry_exhausts_then_raises(monkeypatch):
    monkeypatch.setattr(s.time, "sleep", lambda *_: None)
    s._stats["reranker"]["load_retries"] = 0

    def loader():
        raise RuntimeError("out of memory: OOM")  # 始终瞬时失败

    with pytest.raises(RuntimeError, match="OOM"):
        s._load_with_retry("reranker", loader, max_attempts=3)
    # 3 次尝试 = 2 次重试记录
    assert s._stats["reranker"]["load_retries"] == 2


def test_load_with_retry_non_transient_raises_immediately(monkeypatch):
    slept = {"n": 0}
    monkeypatch.setattr(s.time, "sleep", lambda *_: slept.__setitem__("n", slept["n"] + 1))
    s._stats["embedding"]["load_retries"] = 0
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        raise FileNotFoundError("model path missing")  # 非瞬时 → 不重试

    with pytest.raises(FileNotFoundError):
        s._load_with_retry("embedding", loader, max_attempts=3)
    assert calls["n"] == 1            # 只试一次
    assert slept["n"] == 0            # 没退避
    assert s._stats["embedding"]["load_retries"] == 0
