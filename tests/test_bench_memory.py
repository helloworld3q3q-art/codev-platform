"""bench_memory.percentiles 纯函数单测(压测延迟统计;真延迟由 WSL 实跑)。"""
from __future__ import annotations

from scripts.bench_memory import percentiles


def test_empty_all_zero():
    assert percentiles([]) == {50: 0.0, 95: 0.0, 99: 0.0}


def test_nearest_rank_ms_conversion():
    # 1ms..10ms 共 10 条:p50→第5条(5ms),p95/p99→第10条(10ms)
    samples = [i / 1000 for i in range(1, 11)]
    out = percentiles(samples)
    assert out[50] == 5.0
    assert out[95] == 10.0 and out[99] == 10.0


def test_single_sample():
    assert percentiles([0.002]) == {50: 2.0, 95: 2.0, 99: 2.0}


def test_custom_ps():
    out = percentiles([0.01, 0.02, 0.03, 0.04], ps=(25, 75))
    assert set(out.keys()) == {25, 75} and out[75] >= out[25]
