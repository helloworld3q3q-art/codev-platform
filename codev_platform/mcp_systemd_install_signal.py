"""全量 systemd 安装事务的 SIGINT 延后守卫。"""

from __future__ import annotations

import signal
import threading
from dataclasses import dataclass

from codev_platform.mcp_systemd_install_contract import SystemdInstallTransactionError


# 事务补偿、处理器交接与异常结算必须按同一终止优先级处理。
TERMINATION_EXCEPTIONS = (KeyboardInterrupt, SystemExit, MemoryError)


@dataclass(slots=True)
class DeferredSigintGuard:
    """在可变事务期间仅记录 SIGINT，待全部结算后再按原处理器重放一次。"""

    _previous_handler: object | None = None
    _armed: bool = False
    count: int = 0

    @property
    def pending(self) -> bool:
        return self.count > 0

    def arm(self) -> None:
        if self._armed:
            return
        if threading.current_thread() is not threading.main_thread():
            raise SystemdInstallTransactionError("SIGINT 延后守卫只能在主线程建立")
        try:
            previous_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, self._record)
        except (OSError, ValueError) as error:
            raise SystemdInstallTransactionError("无法建立 SIGINT 延后守卫") from error
        self._previous_handler = previous_handler
        self._armed = True

    def close(self) -> BaseException | None:
        if not self._armed:
            return None
        try:
            signal.signal(signal.SIGINT, self._previous_handler)
        except (OSError, ValueError) as error:
            restore_error = SystemdInstallTransactionError("无法恢复 SIGINT 原处理器")
            restore_error.__cause__ = error
            return restore_error
        finally:
            self._armed = False
        return None

    def raise_if_pending(self) -> None:
        if self.pending:
            raise self.make_interrupt()

    def make_interrupt(self) -> KeyboardInterrupt:
        return KeyboardInterrupt("收到 Ctrl+C，已等待 systemd 事务收敛")

    def replay_once(self) -> None:
        previous_handler = self._previous_handler
        if callable(previous_handler):
            previous_handler(signal.SIGINT, None)
            return
        if previous_handler is not signal.SIG_IGN:
            raise self.make_interrupt()

    def _record(self, _signum: int, _frame: object) -> None:
        self.count += 1
