class SystemdConditionSyntaxError(RuntimeError):
    """未知 systemd drop-in 的条件语法无法安全证明。"""


class UnknownSystemdConditionDirectiveError(SystemdConditionSyntaxError):
    """未知 systemd drop-in 声明或重置了条件。"""


def verify_no_unknown_condition_directives(content: bytes) -> None:
    if type(content) is not bytes:
        raise SystemdConditionSyntaxError("systemd drop-in 内容类型无效")
    normalized = content.replace(b"\r\n", b"\n")
    try:
        text = normalized.decode("utf-8", "strict")
    except UnicodeError:
        raise SystemdConditionSyntaxError("systemd drop-in 不是严格 UTF-8") from None
    if b"\r" in normalized or b"\\" in normalized or b"\x00" in normalized or "\ufeff" in text:
        raise SystemdConditionSyntaxError("systemd drop-in 物理语法不可证明")
    for line in normalized.split(b"\n"):
        stripped = line.strip(b" \t")
        if not stripped or stripped.startswith((b"#", b";", b"[")):
            continue
        key, separator, _value = stripped.partition(b"=")
        if separator and key.strip(b" \t").startswith((b"Condition", b"Assert")):
            raise UnknownSystemdConditionDirectiveError("systemd drop-in 含未知条件或重置")
