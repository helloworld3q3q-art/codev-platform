"""运行时分域 store 共享的公共输入错误收敛原语。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar, cast


_Input = TypeVar("_Input")


@dataclass(frozen=True, slots=True)
class StorePublicInputValidator:
    """将一个分域 store 的领域输入错误收敛为统一公共错误。"""

    error_type: type[Exception]

    def encode(
        self,
        value: _Input,
        encoder: Callable[[_Input], bytes],
        *,
        label: str,
    ) -> bytes:
        """把领域对象编码失败转换为当前分域的稳定错误。"""
        try:
            return encoder(value)
        except ValueError as error:
            raise self.error_type(f"{label} 参数无效") from error

    def require_type(
        self,
        value: object,
        expected: type[_Input],
        *,
        label: str,
    ) -> _Input:
        """在访问 companion 字段前拒绝错误公共对象类型。"""
        if type(value) is not expected:
            raise self.error_type(f"{label} 类型无效")
        return cast(_Input, value)


__all__ = ["StorePublicInputValidator"]
