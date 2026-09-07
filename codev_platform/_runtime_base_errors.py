"""依赖基座构建与完整性校验的公开错误契约。"""

from __future__ import annotations


class RuntimeBaseError(RuntimeError):
    """依赖基座无法构建或验证。"""


class RuntimeBaseIntegrityError(RuntimeBaseError):
    """依赖基座的持久内容不再符合元数据。"""


__all__ = ["RuntimeBaseError", "RuntimeBaseIntegrityError"]
