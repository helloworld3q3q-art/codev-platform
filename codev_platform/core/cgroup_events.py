"""与业务域无关的 cgroup.events 严格解析。"""

from __future__ import annotations


_MAX_CONTROL_BYTES = 1024 * 1024


def parse_cgroup_events(raw: bytes) -> bool:
    """严格解析 cgroup.events，返回 populated 是否为 1。"""
    if type(raw) is not bytes or not 0 < len(raw) <= _MAX_CONTROL_BYTES:
        raise ValueError("cgroup.events 大小无效")
    try:
        text = raw.decode("ascii")
    except UnicodeError:
        raise ValueError("cgroup.events 不是 ASCII") from None
    values: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[0].replace("_", "").isalnum():
            raise ValueError("cgroup.events 行格式无效")
        if parts[0] in values or not parts[1].isdigit():
            raise ValueError("cgroup.events 含重复键或非整数")
        values[parts[0]] = parts[1]
    if values.get("populated") not in {"0", "1"}:
        raise ValueError("cgroup.events 缺少合法 populated")
    return values["populated"] == "1"


__all__ = ["parse_cgroup_events"]
