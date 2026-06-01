"""chroma daemon —— 纯工具 helper (从 server.py 抽出, file-discipline §1)。

退避重试 / GPU 错误判定 / 显存查询 / dtype 解析 / ISO 时间。除 _load_with_retry
(记重试次数进 _stats + 写日志) 外均为纯函数。无可变共享态 rebinding。
"""
from __future__ import annotations

import time
from typing import Any

from ._obslog import _flog
from ._stats import _stats


def _torch_dtype(name: str, device: str):
    """dtype 名 → torch dtype。auto: cuda 系→fp16, 否则 fp32(兼容旧行为)。"""
    import torch
    n = (name or "auto").strip().lower()
    if n == "auto":
        return torch.float16 if str(device).startswith("cuda") else torch.float32
    return {
        "float16": torch.float16, "fp16": torch.float16,
        "bfloat16": torch.bfloat16, "bf16": torch.bfloat16,
        "float32": torch.float32, "fp32": torch.float32,
    }.get(n, torch.float32)


def retry_delays(attempts: int, base: float = 0.5, cap: float = 2.0) -> list[float]:
    """退避序列 (纯函数, 可测): 指数增长 base*2^i, 各项 clamp 到 cap。

    返回 len == max(0, attempts - 1) 个 sleep 间隔 (n 次尝试之间 n-1 次 sleep)。
    例: retry_delays(3, 0.5, 2.0) -> [0.5, 1.0]; retry_delays(4) -> [0.5, 1.0, 2.0]。
    总退避有上限 (sum)，不会无限阻塞。
    """
    if attempts <= 1:
        return []
    out: list[float] = []
    for i in range(attempts - 1):
        out.append(min(base * (2.0 ** i), cap))
    return out


def should_retry(attempt: int, max_attempts: int, exc: BaseException) -> bool:
    """是否再试: 未到 max_attempts 且异常看起来是瞬时 (GPU busy / transient OOM)。

    attempt 从 1 计数 (第 1 次尝试 attempt=1)。非瞬时错误 (如路径不存在 / import 失败)
    不重试 —— 重试也不会变好, 直接进降级。
    """
    if attempt >= max_attempts:
        return False
    return _is_gpu_error(exc)


def _load_with_retry(label: str, loader, max_attempts: int = 3,
                     base: float = 0.5, cap: float = 2.0):
    """有界重试包装 GPU 模型懒加载。

    loader: 无参 callable, 成功返回非 None 结果, 失败抛异常。
    瞬时失败 (GPU busy / transient OOM) 按 retry_delays 退避重试; 非瞬时 / 耗尽 → 抛最后异常。
    记重试次数进 _stats[label]['load_retries'] 供 health 观测。
    note: 同步 sleep, 但总退避 <= sum(retry_delays) (默认 1.5s), 不长阻塞事件循环。
    """
    delays = retry_delays(max_attempts, base, cap)
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return loader()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not should_retry(attempt, max_attempts, exc):
                raise
            s = _stats.get(label)
            if s is not None:
                s["load_retries"] = int(s.get("load_retries", 0)) + 1
            delay = delays[attempt - 1] if attempt - 1 < len(delays) else cap
            _flog(f"[{label}] load attempt {attempt}/{max_attempts} failed "
                  f"({type(exc).__name__}: {exc}); transient, retry in {delay}s")
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _is_gpu_error(exc: BaseException) -> bool:
    """粗判异常是否 CUDA 显存 / 设备类错误 (用于 CPU 降级决策)。"""
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(
        kw in text
        for kw in ("cuda", "out of memory", "oom", "device-side", "no kernel image", "nvml")
    )


def _gpu_free_info() -> dict[str, Any] | None:
    """整卡 free / 本进程外占用 (MiB)。非 CUDA / 不可用返回 None。

    free_mb = 整卡空闲; allocated_other_mb = 整卡已用 - 本进程已分配 (粗估其它进程占用)。
    """
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        free_b, total_b = torch.cuda.mem_get_info()
        free_mb = round(free_b / (1024 * 1024), 1)
        used_total_mb = round((total_b - free_b) / (1024 * 1024), 1)
        self_mb = round(torch.cuda.memory_allocated() / (1024 * 1024), 1)
        return {
            "free_mb": free_mb,
            "allocated_other_mb": round(max(0.0, used_total_mb - self_mb), 1),
        }
    except Exception:
        return None


def _gpu_memory_mb() -> float | None:
    """返回 CUDA 当前已分配显存 (MiB), 不可用 / 非 CUDA 返回 None。

    口径限制: 仅统计 daemon 当前 Python 进程 — 不含其它进程 (cross-link MCP / 别的占用).
    用于 widget 观测 daemon 自身负载, 不等同整卡占用. 整卡 free/used 走
    torch.cuda.mem_get_info(), 后续 Phase 2 可暴露。
    """
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        return round(torch.cuda.memory_allocated() / (1024 * 1024), 1)
    except Exception:
        return None


def _to_iso(epoch_sec: float | None) -> str | None:
    if epoch_sec is None:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(epoch_sec, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    except Exception:
        return None
