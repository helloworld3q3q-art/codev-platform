"""Pg queue 的无秘密连接描述符与 schema 冻结标识。"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from urllib.parse import parse_qsl, unquote, urlsplit

_IDENTIFIER_RE = re.compile(r"[a-z_][a-z0-9_]*\Z")
_HOST_RE = re.compile(r"[a-z0-9][a-z0-9.:-]{0,252}\Z")
_DATABASE_RE = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
_BASE_LOCATOR_RE = re.compile(
    r"pg-v2-base\|host=[a-z0-9][a-z0-9.:-]{0,252}\|port=[1-9][0-9]{0,4}"
    r"\|db=[A-Za-z0-9_.-]{1,128}\Z"
)
_LOCATOR_RE = re.compile(
    r"pg-v2\|host=[a-z0-9][a-z0-9.:-]{0,252}\|port=[1-9][0-9]{0,4}"
    r"\|db=[A-Za-z0-9_.-]{1,128}\|schema=[a-z_][a-z0-9_]*"
    r"\|table=[a-z_][a-z0-9_]*\Z"
)
_UNSAFE_CONNECTION_KEYS = frozenset({
    "host", "hostaddr", "port", "dbname", "database", "service",
    "options", "search_path",
})
_FORBIDDEN_KEYWORD_KEYS = frozenset({"hostaddr", "service", "options", "search_path"})
_SINGLE_TARGET_KEYWORD_KEYS = frozenset({"host", "port", "dbname", "database"})
_MAX_PG_IDENTIFIER_BYTES = 63


@dataclass(frozen=True, slots=True)
class FrozenPgQueueBinding:
    """已在真实连接内冻结的物理队列目标。"""

    locator: str
    schema: str
    table: str
    qualified_table: str
    qualified_index: str


def _conninfo_to_dict(dsn: str) -> dict[str, str]:
    from psycopg.conninfo import conninfo_to_dict

    return dict(conninfo_to_dict(dsn))


def bare_table_name(value: object) -> str:
    """校验构造期允许保存的裸表名。"""
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError("PG queue 表名无效")
    return value


def _schema_name(value: object) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError("PG queue 当前 schema 无效")
    return value


def _descriptor(host: object, port: object, database: object) -> str:
    if type(host) is not str or _HOST_RE.fullmatch(host.lower()) is None:
        raise ValueError("PG queue 绑定主机无效")
    try:
        resolved_port = int(port)
    except (TypeError, ValueError):
        raise ValueError("PG queue 绑定端口无效") from None
    if not 1 <= resolved_port <= 65535:
        raise ValueError("PG queue 绑定端口无效")
    if type(database) is not str or _DATABASE_RE.fullmatch(database) is None:
        raise ValueError("PG queue 绑定数据库无效")
    return f"pg-v2-base|host={host.lower()}|port={resolved_port}|db={database}"


def _url_components(dsn: str) -> tuple[object, object, object, list[tuple[str, str]]]:
    try:
        parsed = urlsplit(dsn)
        host = parsed.hostname
        port = parsed.port or 5432
        query = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError:
        pass
    else:
        if parsed.scheme.lower() not in {"postgres", "postgresql"} or parsed.fragment:
            raise ValueError("PG queue 绑定连接串协议无效")
        database = unquote(parsed.path[1:]) if parsed.path.startswith("/") else ""
        return host, port, database, query
    raise ValueError("PG queue 绑定连接串无法解析") from None


def _url_base_locator(dsn: str) -> str:
    host, port, database, query = _url_components(dsn)
    if any(key.lower() in _UNSAFE_CONNECTION_KEYS for key, _value in query):
        raise ValueError("PG queue 绑定不接受连接目标覆盖项")
    return _descriptor(host, port, database)


def _keyword_option_keys(dsn: str) -> tuple[str, ...]:
    """按 libpq keyword 语法跳过引号/转义值，仅提取顶层 option key。"""
    keys: list[str] = []
    index = 0
    length = len(dsn)
    while index < length:
        while index < length and dsn[index].isspace():
            index += 1
        if index == length:
            break
        start = index
        while index < length and not dsn[index].isspace() and dsn[index] != "=":
            index += 1
        key = dsn[start:index]
        while index < length and dsn[index].isspace():
            index += 1
        if not key or index == length or dsn[index] != "=":
            raise ValueError("PG queue 绑定连接串无法解析")
        index += 1
        while index < length and dsn[index].isspace():
            index += 1
        if index < length and dsn[index] == "'":
            index += 1
            closed = False
            while index < length:
                if dsn[index] == "\\":
                    index += 2
                elif dsn[index] == "'":
                    index += 1
                    closed = True
                    break
                else:
                    index += 1
            if not closed or index > length:
                raise ValueError("PG queue 绑定连接串无法解析")
            if index < length and not dsn[index].isspace():
                raise ValueError("PG queue 绑定连接串无法解析")
        else:
            while index < length and not dsn[index].isspace():
                if dsn[index] == "\\":
                    index += 2
                else:
                    index += 1
            if index > length:
                raise ValueError("PG queue 绑定连接串无法解析")
        keys.append(key.lower())
    return tuple(keys)


def _keyword_values(dsn: str) -> dict[str, str]:
    try:
        values = _conninfo_to_dict(dsn)
    except Exception:  # noqa: BLE001 - 解析器异常可能包含原始 DSN
        values = None
    if values is None:
        raise ValueError("PG queue 绑定连接串无法解析") from None
    return values


def _keyword_base_locator(dsn: str) -> str:
    keys = _keyword_option_keys(dsn)
    if any(key in _FORBIDDEN_KEYWORD_KEYS for key in keys):
        raise ValueError("PG queue 绑定不接受连接目标覆盖项")
    if any(keys.count(key) > 1 for key in _SINGLE_TARGET_KEYWORD_KEYS):
        raise ValueError("PG queue 绑定不接受重复连接目标")
    if "dbname" in keys and "database" in keys:
        raise ValueError("PG queue 绑定不接受重复连接目标")
    values = _keyword_values(dsn)
    if any(key in values for key in _UNSAFE_CONNECTION_KEYS - {"host", "port", "dbname", "database"}):
        raise ValueError("PG queue 绑定不接受连接目标覆盖项")
    host = values.get("host")
    if type(host) is str and "," in host:
        raise ValueError("PG queue 绑定不接受多 host")
    return _descriptor(
        host,
        values.get("port") or "5432",
        values.get("dbname") or values.get("database"),
    )


def pg_binding_base_locator(dsn: str) -> str:
    """提取不带 schema/table 的无秘密 PG 连接描述符。"""
    if (
        type(dsn) is not str
        or not dsn
        or any(ord(char) < 32 or ord(char) == 127 for char in dsn)
    ):
        raise ValueError("PG queue 绑定连接串无效")
    return _url_base_locator(dsn) if "://" in dsn else _keyword_base_locator(dsn)


def _stored_base_locator(value: object) -> str:
    if type(value) is not str or _BASE_LOCATOR_RE.fullmatch(value) is None:
        raise ValueError("PG queue 基础绑定描述符无效")
    port = int(value.split("|", 3)[2].removeprefix("port="))
    if not 1 <= port <= 65535:
        raise ValueError("PG queue 基础绑定描述符端口无效")
    return value


def _quoted_identifier(value: str) -> str:
    """值已通过严格 ASCII 标识符校验，因此无需转义拼接。"""
    return f'"{value}"'


def _index_name(table: str) -> str:
    candidate = f"ix_{table}_claim"
    if len(candidate.encode("ascii")) <= _MAX_PG_IDENTIFIER_BYTES:
        return candidate
    digest = sha256(table.encode("ascii")).hexdigest()[:16]
    prefix_limit = _MAX_PG_IDENTIFIER_BYTES - len("ix___claim") - len(digest)
    return f"ix_{table[:prefix_limit]}_{digest}_claim"


def freeze_pg_queue_binding(
    base_locator: object,
    schema: object,
    table: object,
) -> FrozenPgQueueBinding:
    """以当前连接实际 schema 生成唯一且安全的物理目标。"""
    base = _stored_base_locator(base_locator)
    resolved_schema = _schema_name(schema)
    resolved_table = bare_table_name(table)
    endpoint = base.removeprefix("pg-v2-base")
    locator = (
        f"pg-v2{endpoint}|schema={resolved_schema}|table={resolved_table}"
    )
    stored_owner_binding_locator(locator)
    return FrozenPgQueueBinding(
        locator=locator,
        schema=resolved_schema,
        table=resolved_table,
        qualified_table=(
            f"{_quoted_identifier(resolved_schema)}.{_quoted_identifier(resolved_table)}"
        ),
        qualified_index=(
            f"{_quoted_identifier(resolved_schema)}.{_quoted_identifier(_index_name(resolved_table))}"
        ),
    )


def owner_binding_locator(dsn: str, table: str) -> str:
    """兼容旧调用，但拒绝在未读取当前 schema 时生成 owner 绑定。"""
    pg_binding_base_locator(dsn)
    bare_table_name(table)
    raise ValueError("PG owner 绑定必须在 schema 冻结后获取")


def stored_owner_binding_locator(value: object) -> str:
    """校验已冻结描述符，拒绝把原始 DSN 当作 locator 使用。"""
    if type(value) is not str or _LOCATOR_RE.fullmatch(value) is None:
        raise ValueError("PG owner 绑定描述符无效")
    port = int(value.split("|", 3)[2].removeprefix("port="))
    if not 1 <= port <= 65535:
        raise ValueError("PG owner 绑定描述符端口无效")
    return value


__all__ = [
    "FrozenPgQueueBinding", "bare_table_name", "freeze_pg_queue_binding",
    "owner_binding_locator", "pg_binding_base_locator", "stored_owner_binding_locator",
]
