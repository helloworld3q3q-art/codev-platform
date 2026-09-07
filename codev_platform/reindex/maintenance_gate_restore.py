"""维护门禁对恢复待命状态机暴露的窄门面。"""

from __future__ import annotations

import time
from pathlib import Path


def read_maintenance_gate_record(path: Path | None = None):
    """读取 marker 的受控状态；状态机实现与底层锁存储分离。"""
    from .maintenance_gate_state import read_maintenance_gate_record as read_record

    return read_record(path=path)


def arm_restore_standby(*, ttl_sec: float, path: Path | None = None):
    """预备仅待命、不授予写权限的短时恢复状态。"""
    from .maintenance_gate_state import arm_restore_standby as arm

    return arm(ttl_sec=ttl_sec, path=path)


def claim_restore_standby(*, generation: str, invocation_id: str, path: Path | None = None):
    """绑定本次 systemd invocation，防止非本次待命实例交接。"""
    from .maintenance_gate_state import claim_restore_standby as claim

    return claim(generation=generation, invocation_id=invocation_id, path=path)


def renew_claimed_restore_standby(
    *,
    generation: str,
    invocation_id: str,
    ttl_sec: float,
    path: Path | None = None,
):
    """只为已绑定的同一待命实例续租，绝不放开通用写权限。"""
    from .maintenance_gate_state import renew_claimed_restore_standby as renew

    return renew(
        generation=generation,
        invocation_id=invocation_id,
        ttl_sec=ttl_sec,
        path=path,
    )


def renew_claimed_restore_standby_while_systemd_transition_locked(
    *,
    generation: str,
    invocation_id: str,
    ttl_sec: float,
):
    """已持转换 EX 时续租待命身份，不重入同一 flock。"""
    from .maintenance_gate_state import (
        renew_claimed_restore_standby_while_systemd_transition_locked as renew,
    )

    return renew(
        generation=generation,
        invocation_id=invocation_id,
        ttl_sec=ttl_sec,
    )


def complete_restore_standby(
    *,
    generation: str,
    invocation_id: str,
    path: Path | None = None,
) -> None:
    """完成验证后才最终移除 marker，交接 normal 写入权限。"""
    from .maintenance_gate_state import complete_restore_standby as complete

    complete(generation=generation, invocation_id=invocation_id, path=path)


def complete_restore_standby_while_systemd_transition_locked(
    *,
    generation: str,
    invocation_id: str,
) -> None:
    """已持转换 EX 时完成待命交接，不重入同一 flock。"""
    from .maintenance_gate_state import (
        complete_restore_standby_while_systemd_transition_locked as complete,
    )

    complete(generation=generation, invocation_id=invocation_id)


def maintenance_restore_standby_permit() -> bool:
    """仅供 service 等待恢复交接；它不代表任何写权限。"""
    from .maintenance_gate_state import maintenance_restore_standby_permit as permit

    return permit()


def wait_for_restore_standby_release(
    *,
    poll_sec: float = 0.2,
    sleeper=time.sleep,
) -> bool:
    """等待 root 最终删除 marker；返回真后调用方才可进入正常写初始化。"""
    from .maintenance_gate_state import wait_for_restore_standby_release as wait

    return wait(poll_sec=poll_sec, sleeper=sleeper)


def maintenance_admin_window_permit():
    """在维护稳态持有全局 gate 独占锁，串行管理员实际写阶段。"""
    from .maintenance_gate_state import maintenance_admin_window_permit as permit

    return permit()


__all__ = [
    "arm_restore_standby",
    "claim_restore_standby",
    "complete_restore_standby",
    "complete_restore_standby_while_systemd_transition_locked",
    "maintenance_admin_window_permit",
    "maintenance_restore_standby_permit",
    "read_maintenance_gate_record",
    "renew_claimed_restore_standby",
    "renew_claimed_restore_standby_while_systemd_transition_locked",
    "wait_for_restore_standby_release",
]
