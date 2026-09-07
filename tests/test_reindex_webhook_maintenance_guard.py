"""Webhook 维护条件 guard 的可信写入与有效解析回归。"""

from __future__ import annotations

from types import SimpleNamespace


def _snapshot(module):
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        RootOwnedRegularFileSnapshot,
    )

    return RootOwnedRegularFileSnapshot(
        module.WEBHOOK_MAINTENANCE_GUARD_DROPIN_CONTENT,
        0o644,
        0,
        0,
    )


def test_guard写入reload后必须进入Webhook的有效条件解析() -> None:
    from codev_platform.ops import reindex_webhook_maintenance_guard as module

    events: list[object] = []

    def write(path, content, *, mode, uid, gid) -> None:
        events.append(("write", path, content, mode, uid, gid))

    def read(path, **_kwargs):
        events.append(("read", path))
        return _snapshot(module)

    def run(command, **_kwargs):
        events.append(("systemctl", command))
        output = (
            f"DropInPaths={module.WEBHOOK_MAINTENANCE_GUARD_DROPIN_PATH.as_posix()}\n"
            if command[0] == "/usr/bin/systemctl"
            else ""
        )
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    module.ensure_webhook_maintenance_guard(
        platform_name="linux",
        writer=write,
        reader=read,
        command_runner=run,
    )

    assert events[0] == (
        "write",
        module.WEBHOOK_MAINTENANCE_GUARD_DROPIN_PATH,
        module.WEBHOOK_MAINTENANCE_GUARD_DROPIN_CONTENT,
        0o644,
        0,
        0,
    )
    assert ("systemctl", ("systemctl", "daemon-reload")) in events
    assert (
        "systemctl",
        ("/usr/bin/systemctl", "show", "codev-webhook.service", "--property=DropInPaths"),
    ) in events
