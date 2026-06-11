"""共享 reindex 写侧锁单测 (chroma/_reindex_lock, R2 抽出)。"""
from __future__ import annotations

import os
import time

from codev_platform.chroma._reindex_lock import (
    release_reindex_lock,
    try_acquire_reindex_lock,
)


def test_acquire_then_busy_then_release(tmp_path):
    lock = try_acquire_reindex_lock(tmp_path)
    assert lock is not None                       # 首次拿到
    assert try_acquire_reindex_lock(tmp_path) is None   # 已占 → 拒绝
    release_reindex_lock(lock)
    lock2 = try_acquire_reindex_lock(tmp_path)     # 释放后可再拿
    assert lock2 is not None
    release_reindex_lock(lock2)


def test_stale_lock_preempted(tmp_path):
    # 模拟**死进程**残留的锁: 文件存在但无人持有 fd(Windows 才能删/抢占; 活进程持锁不该被抢)。
    lock_file = tmp_path / ".reindex.lock"
    lock_file.write_text("99999\n0\n", encoding="utf-8")
    old = time.time() - 31 * 60                    # 31 分钟前(> 30min stale 阈值)
    os.utime(lock_file, (old, old))
    preempted = try_acquire_reindex_lock(tmp_path)  # stale + 无人持有 → 抢占成功
    assert preempted is not None
    release_reindex_lock(preempted)


def test_release_none_is_noop():
    release_reindex_lock(None)   # 不抛
