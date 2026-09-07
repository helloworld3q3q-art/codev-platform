"""隔离 reindex 仓向量的进程内状态与严格跨进程 codec。"""
from __future__ import annotations

import hmac
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from codev_platform.core.config import (
    REINDEX_CONFIG_DIGEST_ENV,
    config_snapshot_digest,
)
from codev_platform.core.project_id import validate as validate_project_id

REINDEX_REPO_OVERRIDE_ENV = "CODEV_REINDEX_REPO_OVERRIDE"
_MAX_PAYLOAD_BYTES = 16 * 1024
_RUNTIME_STATE_KEY = object()
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class RepoOverrideEntry:
    """与仓目录实现解耦的规范仓向量记录。"""

    root: Path
    tag: str
    is_main: bool
    source_project_id: str | None


def _project_id(value: object) -> str:
    if type(value) is not str:
        raise ValueError("repo override project_id 无效")
    try:
        normalized = validate_project_id(value)
    except Exception:
        raise ValueError("repo override project_id 无效") from None
    if normalized != value:
        raise ValueError("repo override project_id 必须是规范值")
    return normalized


def _source_project_id(value: object) -> str | None:
    return None if value is None else _project_id(value)


def _payload_size(raw: str) -> int:
    try:
        return len(raw.encode("utf-8"))
    except UnicodeError:
        raise ValueError("repo override 不是有效 UTF-8") from None


def _strict_json_object(raw: str) -> dict[str, object]:
    if type(raw) is not str or _payload_size(raw) > _MAX_PAYLOAD_BYTES:
        raise ValueError("repo override JSON 无效或超过大小上限")

    def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("repo override JSON 包含重复键")
            value[key] = item
        return value

    def _reject_constant(_value: str) -> object:
        raise ValueError("repo override JSON 包含非有限数")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        raise ValueError("repo override JSON 无效") from None
    if type(value) is not dict:
        raise ValueError("repo override JSON 根必须是对象")
    return value


def _digest(value: object, field: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"repo override {field} 摘要无效")
    return value


def _verify_digest_binding(payload_digest: object, cfg: dict) -> None:
    encoded_digest = _digest(payload_digest, "payload")
    environment_digest = _digest(
        os.environ.get(REINDEX_CONFIG_DIGEST_ENV),
        "environment",
    )
    actual_digest = _digest(config_snapshot_digest(cfg), "cfg")
    payload_matches = hmac.compare_digest(encoded_digest, environment_digest)
    config_matches = hmac.compare_digest(environment_digest, actual_digest)
    if not payload_matches or not config_matches:
        raise ValueError("repo override 配置摘要不一致")


def normalize_entries(
    project_id: str,
    entries: list[RepoOverrideEntry] | tuple[RepoOverrideEntry, ...],
) -> tuple[RepoOverrideEntry, ...]:
    """校验并规范化仓记录，拒绝路径、tag 与主仓身份漂移。"""
    _project_id(project_id)
    normalized: list[RepoOverrideEntry] = []
    roots: set[Path] = set()
    keys: set[str] = set()
    main_count = 0
    for item in entries:
        if type(item) is not RepoOverrideEntry or type(item.is_main) is not bool:
            raise ValueError("repo override 仓描述无效")
        try:
            declared_root = Path(item.root)
            root = declared_root.resolve(strict=True)
        except (OSError, RuntimeError, TypeError):
            raise ValueError("repo override 仓根不可用") from None
        if not declared_root.is_absolute() or declared_root != root:
            raise ValueError("repo override 仓根必须是规范绝对路径")
        if not root.is_dir() or root in roots:
            raise ValueError("repo override 仓根无效或重复")
        tag = item.tag
        if type(tag) is not str or "\x00" in tag:
            raise ValueError("repo override tag 无效")
        key = "main" if item.is_main else tag
        if (item.is_main and tag) or (not item.is_main and not tag) or key in keys:
            raise ValueError("repo override tag 缺失或重复")
        roots.add(root)
        keys.add(key)
        main_count += int(item.is_main)
        normalized.append(RepoOverrideEntry(
            root=root,
            tag=tag,
            is_main=item.is_main,
            source_project_id=_source_project_id(item.source_project_id),
        ))
    if not normalized or main_count != 1 or not normalized[0].is_main:
        raise ValueError("repo override 主仓缺失或不唯一")
    return tuple(normalized)


def install_runtime_override(
    cfg: dict,
    project_id: str,
    entries: list[RepoOverrideEntry] | tuple[RepoOverrideEntry, ...],
) -> None:
    """把规范仓向量保存到 cfg 的进程私有对象键。"""
    if type(cfg) is not dict:
        raise ValueError("runtime repo override cfg 无效")
    project = _project_id(project_id)
    normalized = normalize_entries(project, entries)
    overrides = cfg.get(_RUNTIME_STATE_KEY)
    if type(overrides) is not dict:
        overrides = {}
        cfg[_RUNTIME_STATE_KEY] = overrides
    overrides[project] = normalized


def runtime_override(cfg: dict, project_id: str) -> tuple[RepoOverrideEntry, ...] | None:
    """读取并重新校验进程内仓向量。"""
    if type(cfg) is not dict:
        raise ValueError("runtime repo override cfg 无效")
    project = _project_id(project_id)
    overrides = cfg.get(_RUNTIME_STATE_KEY)
    if type(overrides) is not dict or project not in overrides:
        return None
    value = overrides[project]
    if type(value) is not tuple:
        raise ValueError("runtime repo override 内部值无效")
    return normalize_entries(project, value)


def override_environment(cfg: dict, project_id: str) -> dict[str, str]:
    """编码无秘密、限长且绑定配置摘要的子进程仓向量。"""
    project = _project_id(project_id)
    entries = runtime_override(cfg, project)
    if entries is None:
        return {}
    payload = {
        "schema_version": 1,
        "project_id": project,
        "config_sha256": config_snapshot_digest(cfg),
        "repos": [
            {
                "root": str(item.root),
                "tag": item.tag,
                "is_main": item.is_main,
                "source_project_id": item.source_project_id,
            }
            for item in entries
        ],
    }
    try:
        raw = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise ValueError("repo override 无法规范编码") from None
    if _payload_size(raw) > _MAX_PAYLOAD_BYTES:
        raise ValueError("repo override 超过子进程环境上限")
    return {REINDEX_REPO_OVERRIDE_ENV: raw}


def override_from_environment(
    cfg: dict,
    project_id: str,
) -> tuple[RepoOverrideEntry, ...] | None:
    """严格解码并验证 payload、环境与传入 cfg 的三方摘要。"""
    raw = os.environ.get(REINDEX_REPO_OVERRIDE_ENV)
    if raw is None:
        return None
    project = _project_id(project_id)
    value = _strict_json_object(raw)
    expected_fields = {"schema_version", "project_id", "config_sha256", "repos"}
    if set(value) != expected_fields:
        raise ValueError("repo override 字段集合无效")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("repo override schema 无效")
    if value["project_id"] != project:
        raise ValueError("repo override 项目身份不匹配")
    _verify_digest_binding(value["config_sha256"], cfg)
    raw_entries = value["repos"]
    if type(raw_entries) is not list:
        raise ValueError("repo override repos 必须是数组")
    expected_entry_fields = {"root", "tag", "is_main", "source_project_id"}
    entries: list[RepoOverrideEntry] = []
    for raw_entry in raw_entries:
        if type(raw_entry) is not dict or set(raw_entry) != expected_entry_fields:
            raise ValueError("repo override 仓字段无效")
        entries.append(RepoOverrideEntry(
            root=raw_entry["root"],
            tag=raw_entry["tag"],
            is_main=raw_entry["is_main"],
            source_project_id=raw_entry["source_project_id"],
        ))
    return normalize_entries(project, entries)


__all__ = [
    "REINDEX_REPO_OVERRIDE_ENV",
    "RepoOverrideEntry",
    "install_runtime_override",
    "normalize_entries",
    "override_environment",
    "override_from_environment",
    "runtime_override",
]
