"""CodeGraph systemd 实例身份与稳定窗口回归。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_运行身份严格读取InvocationID与重启计数() -> None:
    from codev_platform.ops.reindex_codegraph_runtime_identity import (
        CodegraphRuntimeIdentity,
        read_codegraph_runtime_identity,
    )

    identity = read_codegraph_runtime_identity(
        platform_name="linux",
        command_runner=lambda _command, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                "NRestarts=7\n"
                "SubState=running\n"
                f"InvocationID={'a' * 32}\n"
                "Restart=always\n"
                "ActiveState=active\n"
            ),
        ),
    )

    assert identity == CodegraphRuntimeIdentity("a" * 32, 7)


def test_稳定窗口拒绝实例或重启计数漂移() -> None:
    from codev_platform.ops.reindex_codegraph_runtime_identity import (
        CodegraphRuntimeIdentity,
        CodegraphRuntimeIdentityError,
        prove_codegraph_runtime_stable,
    )

    expected = CodegraphRuntimeIdentity("a" * 32, 2)
    identities = iter((expected, CodegraphRuntimeIdentity("b" * 32, 0)))

    with pytest.raises(CodegraphRuntimeIdentityError, match="稳定窗口"):
        prove_codegraph_runtime_stable(
            expected,
            identity_reader=lambda: next(identities),
            sleeper=lambda _seconds: None,
            sample_count=2,
            interval_sec=0.01,
        )


def test_稳定窗口连续复证同一实例才通过() -> None:
    from codev_platform.ops.reindex_codegraph_runtime_identity import (
        CodegraphRuntimeIdentity,
        prove_codegraph_runtime_stable,
    )

    expected = CodegraphRuntimeIdentity("a" * 32, 2)
    samples: list[CodegraphRuntimeIdentity] = []

    prove_codegraph_runtime_stable(
        expected,
        identity_reader=lambda: samples.append(expected) or expected,
        sleeper=lambda _seconds: None,
        sample_count=3,
        interval_sec=0.01,
    )

    assert samples == [expected, expected, expected]
