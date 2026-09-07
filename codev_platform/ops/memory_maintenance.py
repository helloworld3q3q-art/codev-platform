"""可由已安装 wheel 直接执行的记忆维护入口。"""
from __future__ import annotations

import sys
from collections.abc import Sequence

_MAINT_LOCK_KEY = 0x6D656D31


def main(argv: Sequence[str] | None = None) -> int:
    """执行 TTL 归档，并按可选作用域执行压缩。"""
    from codev_platform.agent import deps

    maint = deps.get_memory_maintenance()
    store = deps.get_memory_store()
    if maint is None or store is None:
        print("[ABORT] memory 未启用(配 memory.pg_dsn + 装 psycopg)。", file=sys.stderr)
        return 2
    args = list(sys.argv[1:] if argv is None else argv)
    with store.advisory_lock(_MAINT_LOCK_KEY) as got:
        if not got:
            print("[SKIP] 另一个 maintenance 实例正在运行(未抢到 advisory lock),本次跳过。")
            return 0
        return _run(maint, args)


def _run(maint, args: Sequence[str]) -> int:
    from codev_platform.agent import deps

    archived = maint.archive_expired()
    print(f"[TTL] 归档过期记忆 {archived} 条")
    if len(args) < 2:
        print("[COMPRESS] 未给 <scope> <scope_ref>,跳过压缩(只做了 TTL 归档)")
        return 0

    scope, scope_ref = args[0], args[1]
    org_id = args[2] if len(args) >= 3 else "default"
    try:
        provider = deps.get_provider()
    except Exception as exc:  # noqa: BLE001 - provider 不可用不阻断 TTL 维护
        print(f"[COMPRESS] 跳过:provider 不可用({exc})", file=sys.stderr)
        return 0
    from codev_platform.agent.memory_maintenance import make_llm_fuse
    result = maint.compress_scope(scope, scope_ref, make_llm_fuse(provider), org_id=org_id)
    if result:
        print(f"[COMPRESS] scope={scope} ref={scope_ref} org={org_id} 融合 {len(result)} 个 topic:")
        for topic_key, entry_id in result.items():
            print(f"  - {topic_key} -> {entry_id}")
    else:
        print(f"[COMPRESS] scope={scope} ref={scope_ref}:无 topic 达到压缩阈值,未动")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
