"""全量 systemd 安装事务的冻结补偿动作与执行器。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from codev_platform.mcp_systemd_install_signal import TERMINATION_EXCEPTIONS


@dataclass(frozen=True, slots=True)
class CompensationAction:
    """冻结的补偿动作；proof 为真时返回值必须为 ``True``。"""

    execute: Callable[[], object]
    proof: bool = False

    def __call__(self) -> object:
        return self.execute()


@dataclass(frozen=True, slots=True)
class CompensationResult:
    """补偿完成后的安全可证明性，以及期间延后处理的终止异常。"""

    proven: bool
    deferred_interrupt: BaseException | None


@dataclass(slots=True)
class CompensationRunner:
    """独立执行补偿分量，避免单项失败或 Ctrl+C 截断其余收敛动作。"""

    proven: bool = True
    deferred_interrupt: BaseException | None = None

    def run(self, action: Callable[[], object]) -> None:
        try:
            result = action()
            if isinstance(action, CompensationAction) and action.proof and result is not True:
                self.proven = False
        except TERMINATION_EXCEPTIONS as error:
            self.defer_interrupt(error)
        except Exception:
            self.proven = False

    def run_all(self, actions: Iterable[Callable[[], object]]) -> None:
        """按冻结顺序耗尽补偿动作；真实 SIGINT 由外层守卫延后。"""
        for action in tuple(actions):
            self.run(action)

    def result(self) -> CompensationResult:
        return CompensationResult(self.proven, self.deferred_interrupt)

    def fail_without_proof(self) -> None:
        self.proven = False

    def defer_interrupt(self, error: BaseException) -> None:
        self.proven = False
        if self.deferred_interrupt is None:
            self.deferred_interrupt = error
