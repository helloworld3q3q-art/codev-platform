"""默认生产端口下的真实目标用户 WSL 集成测试。"""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from codev_platform.runtime_service_access import verify_runtime_service_access
from codev_platform.runtime_target_user_probe import probe_runtime_target_user
from tests.runtime_target_user_verified_support import verified_target_probe_runtime


_SYSTEMD_ROOT_AVAILABLE = (
    os.name == "posix"
    and sys.platform.startswith("linux")
    and hasattr(os, "geteuid")
    and os.geteuid() == 0
    and Path("/run").is_dir()
    and Path("/run/systemd/system").is_dir()
    and Path("/usr/bin/setpriv").is_file()
    and Path("/usr/bin/systemd-run").is_file()
    and Path("/usr/bin/systemctl").is_file()
    and len(tuple(Path("/usr/share/python-wheels").glob("pip-*.whl"))) == 1
    and len(tuple(Path("/usr/share/python-wheels").glob("setuptools-*.whl"))) == 1
)
_SYSTEMD_ROOT_ONLY = pytest.mark.skipif(
    not _SYSTEMD_ROOT_AVAILABLE,
    reason="需要 Linux root、systemd、setpriv、bootstrap wheels 与 /run 临时目录",
)


@_SYSTEMD_ROOT_ONLY
def test_默认生产端口验证真实基座版本服务组与目标用户() -> None:
    with verified_target_probe_runtime() as runtime:
        assert runtime.root.is_relative_to(Path("/run"))
        access = verify_runtime_service_access(
            runtime.root,
            service_uid=runtime.account.uid,
            service_gid=runtime.account.gid,
            base_ids=(runtime.base.base_id,),
            release_ids=(runtime.release.release_id,),
        )
        proof = probe_runtime_target_user(
            runtime.root,
            account=runtime.account,
            base_id=runtime.base.base_id,
            release_id=runtime.release.release_id,
        )

        assert access.content.base_ids == (runtime.base.base_id,)
        assert access.content.release_ids == (runtime.release.release_id,)
        assert proof.base_id == runtime.base.base_id
        assert proof.release_id == runtime.release.release_id
        assert proof.runtime_revision == runtime.release.runtime_revision
        assert proof.service_uid == runtime.account.uid
        assert proof.service_gid == runtime.account.gid
        assert len(proof.evidence_sha256) == 64
