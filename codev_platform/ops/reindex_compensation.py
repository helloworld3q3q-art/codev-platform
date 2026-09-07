"""reindex 状态机共享的穷尽式补偿执行器。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class ExhaustiveCompensationRunner:
    """单项失败不截断后续收敛，终止异常延后到全部动作完成后重抛。"""

    deferred_interruption: BaseException | None = None

    def attempt(self, action: Callable[[], object]) -> bool:
        try:
            action()
            return True
        except BaseException as error:
            if _is_interruption(error) and self.deferred_interruption is None:
                self.deferred_interruption = error
            return False

    def raise_deferred_interruption(self) -> None:
        if self.deferred_interruption is not None:
            raise self.deferred_interruption


def _is_interruption(error: BaseException) -> bool:
    """保留进程终止、任务取消与资源耗尽语义，普通业务异常仅记失败。"""
    return not isinstance(error, Exception) or isinstance(error, MemoryError)


__all__ = ["ExhaustiveCompensationRunner"]
