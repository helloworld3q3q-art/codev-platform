"""版本化运行时跨层共享的稳定错误分类。"""


class RuntimeBuildError(RuntimeError):
    """候选 wheel、依赖基座或薄 release 无法形成可信身份。"""


class RuntimeIdCollisionError(RuntimeError):
    """同一内容 ID 对应了不同的完整身份输入。"""


__all__ = ["RuntimeBuildError", "RuntimeIdCollisionError"]
