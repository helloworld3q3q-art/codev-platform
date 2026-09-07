"""结构化访问审计日志 (jsonl) —— 项目访问授权判定留痕。

纯逻辑 + 一个写入函数, 与 ACL (core/acl.py) 配套:
  - deny 永远记 (安全审计核心: 谁被拒、拒哪个项目、为啥);
  - token allow 记 (真授权留痕: 谁/何时/查哪个项目);
  - passthrough advisory allow 跳过 (dev 放行是噪音, 不污染审计)。

只记身份标识 + 判定, 不记任何密钥/内容 (无 secret, 无需脱敏)。
落 data_root/audit/access.jsonl (随多租户数据走, 与 usage 日志分离)。
写失败静默 —— 审计不得拖垮主流程。
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from codev_platform.core.paths import data_root
from codev_platform.core.runtime_artifact_io import append_runtime_artifact_text


def audit_log_path() -> Path:
    """纯定位审计文件；目录创建与权限收敛只由安全写入边界负责。"""
    return data_root() / "audit" / "access.jsonl"


def audit_access(service: str, identity, project_id, decision) -> None:
    """记一次项目访问授权判定。

    - deny 永远记 (安全审计核心);
    - token allow 记 (真授权留痕: 谁/何时/查哪个项目);
    - passthrough advisory allow 跳过 (dev 放行是噪音, 不污染审计)。
    不记任何密钥/内容, 只记身份标识 + 判定 (无需脱敏: 无 secret)。失败静默 (审计不得拖垮主流程)。
    """
    if decision.allowed and getattr(decision, "advisory", False):
        return
    rec = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "service": service,
        "user_id": getattr(identity, "user_id", None),
        "org_id": getattr(identity, "org_id", None),
        "via": getattr(identity, "via", None),
        "project_id": project_id,
        "allowed": bool(decision.allowed),
        "reason": decision.reason,
    }
    try:
        append_runtime_artifact_text(
            audit_log_path(),
            json.dumps(rec, ensure_ascii=False) + "\n",
        )
    except Exception:  # noqa: BLE001 — 审计写失败不得影响请求
        pass
