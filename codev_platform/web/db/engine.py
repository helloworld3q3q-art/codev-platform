"""SQLAlchemy Engine 接缝 —— RBAC/账户 PG 库的连接出口(替代裸 psycopg ConnectionPool)。

语义对齐原 store:
  - 构造期不连库:`create_engine(...)` 是 lazy 的,首次执行才真正建连接(等价
    `ConnectionPool(open=False)`)。无需 `pool_pre_ping` 之外的预热。
  - 读写分离接缝:`make_engines(dsn, read_dsn)` 返回 (write, read) 两个 engine,read_dsn
    缺省 / 同 dsn 时复用 write engine(对齐 RbacStore._read_pool / _split)。
  - psycopg 缺失契约:平台 venv 无 psycopg → store 构造应抛 ImportError(调用方
    bind_account_stores / get_rbac_store 优雅回退)。SQLAlchemy 默认 driver 解析是 lazy 的
    (要连库才 import 驱动),所以这里**显式探测** psycopg,缺则 ImportError,保证 fail-fast
    时机与旧 ConnectionPool(构造期 import psycopg_pool)一致。

dsn 归一:用户 config 里的 `postgres://` / `postgresql://` 统一补成 `postgresql+psycopg://`
(SQLAlchemy 需要显式 driver;psycopg(3) driver 名为 `psycopg`)。
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def _require_psycopg() -> None:
    """探测 PG 驱动栈是否可用;缺失抛 ImportError(对齐旧 ConnectionPool 构造期行为)。

    平台 venv 无 psycopg 时,这一步替代旧的 `from psycopg_pool import ConnectionPool`,
    让 store __init__ 在缺驱动时立即 ImportError → 调用方优雅回退内存。

    探测 `psycopg_pool`(而非 `psycopg`):与全栈 PG 存储(memory_store_pg / session_pg /
    account_store)的"已安装"标记保持一致 —— 这些模块构造期 import psycopg_pool,缺即回退。
    `pip install -e .[agent]` 会同时拉 psycopg + psycopg_pool,真实部署两者皆在;以 psycopg_pool
    为门可保持迁移前后的回退时机/契约完全不变(失败时机仍在 store __init__,fail-fast 测试不破)。
    SQLAlchemy 的 postgresql+psycopg 实际只需 psycopg(3) driver,部署时已随 .[agent] 装好。
    """
    import psycopg_pool  # noqa: F401


def normalize_dsn(dsn: str) -> str:
    """把 postgres:// / postgresql:// 归一为 SQLAlchemy 需要的 postgresql+psycopg://。

    已带 driver(`postgresql+psycopg://` 或 `sqlite://` 等)则原样返回。
    """
    if dsn.startswith("postgresql+") or dsn.startswith("postgres+"):
        return dsn
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://"):]
    if dsn.startswith("postgres://"):
        return "postgresql+psycopg://" + dsn[len("postgres://"):]
    return dsn


def make_engine(dsn: str, *, pool_size: int = 4) -> Engine:
    """单个 write engine。构造期探测 psycopg(缺则 ImportError),create_engine 本身 lazy 不连库。"""
    _require_psycopg()
    return create_engine(normalize_dsn(dsn), pool_pre_ping=True, pool_size=pool_size)


def make_engines(dsn: str, read_dsn: str | None = None, *, pool_size: int = 4) -> tuple[Engine, Engine]:
    """返回 (write_engine, read_engine)。read_dsn 缺省/同 dsn → read 复用 write(对齐 _split=False)。"""
    write = make_engine(dsn, pool_size=pool_size)
    if read_dsn and read_dsn != dsn:
        read = make_engine(read_dsn, pool_size=pool_size)
    else:
        read = write
    return write, read


__all__ = ["make_engine", "make_engines", "normalize_dsn"]
