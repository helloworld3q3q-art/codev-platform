r"""memory 维护 job(M4):TTL 归档 + 可选压缩融合。手动 / cron / Task Scheduler 调。

跑法(需 config.memory.pg_dsn + psycopg):
  .venv\Scripts\python.exe scripts\run_memory_maintenance.py
      → 只跑 TTL 归档(全 org)

  .venv\Scripts\python.exe scripts\run_memory_maintenance.py <scope> <scope_ref> [org_id]
      → TTL 归档 + 压缩该作用域(同 topic 多条 → LLM 融合 1 条)
      例:... personal local        (压缩用户 local 的个人记忆)
          ... project openclaw-stock

压缩用 config.agent.provider 的真模型融合(缺 key 则跳过压缩,只做 TTL 归档)。
未配 DSN / 缺 psycopg → 优雅退出(exit 2)。
"""
from __future__ import annotations

import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


# maintenance 单实例锁 key(固定常量,任意进程抢到才跑,防并发压同 topic 双写 summary)
_MAINT_LOCK_KEY = 0x6D656D31  # 'mem1'


def main() -> int:
    from codev_platform.agent import deps
    maint = deps.get_memory_maintenance()
    store = deps.get_memory_store()  # 与 maint 同一实例(同连接池),用于加单实例锁
    if maint is None or store is None:
        print("[ABORT] memory 未启用(配 memory.pg_dsn + 装 psycopg)。", file=sys.stderr)
        return 2

    with store.advisory_lock(_MAINT_LOCK_KEY) as got:
        if not got:
            print("[SKIP] 另一个 maintenance 实例正在运行(未抢到 advisory lock),本次跳过。")
            return 0
        return _run(maint, sys.argv[1:])


def _run(maint, args) -> int:
    from codev_platform.agent import deps

    # 1) TTL 归档(总是跑)
    archived = maint.archive_expired()
    print(f"[TTL] 归档过期记忆 {archived} 条")

    # 2) 压缩(给了 scope + scope_ref 才跑)
    if len(args) >= 2:
        scope, scope_ref = args[0], args[1]
        org_id = args[2] if len(args) >= 3 else "default"
        try:
            provider = deps.get_provider()  # 缺 key 抛 RuntimeError
        except Exception as e:  # noqa: BLE001
            print(f"[COMPRESS] 跳过:provider 不可用({e})", file=sys.stderr)
            return 0
        from codev_platform.agent.memory_maintenance import make_llm_fuse
        fuse = make_llm_fuse(provider)
        result = maint.compress_scope(scope, scope_ref, fuse, org_id=org_id)
        if result:
            print(f"[COMPRESS] scope={scope} ref={scope_ref} org={org_id} 融合 {len(result)} 个 topic:")
            for tk, nid in result.items():
                print(f"  - {tk} -> {nid}")
        else:
            print(f"[COMPRESS] scope={scope} ref={scope_ref}:无 topic 达到压缩阈值,未动")
    else:
        print("[COMPRESS] 未给 <scope> <scope_ref>,跳过压缩(只做了 TTL 归档)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
