"""codev_platform.ops.reindex -- cross-platform port of the 4 reindex .ps1 scripts.

Ports (1:1 behavior + exit codes) the Windows-only PowerShell layer into pure
Python CLI subcommands so the same logic runs on Windows / Linux / macOS:

  scripts/update-local-ai.ps1   -> ``reindex``           full / scoped index refresh
  scripts/post-commit.ps1       -> ``post-commit``       git hook: scope-diff + spawn
  scripts/dirty-index-check.ps1 -> ``dirty-check``       dirty work-tree vs index scope
  scripts/wait-for-reindex.ps1  -> ``wait-for-reindex``  block until bg reindex done

All machine paths come from codev_platform.ops._common (config-driven, no
hardcoded paths). Subprocess launching tolerates Windows .cmd/.ps1 launchers and
runs real executables directly elsewhere (see _common.run / resolve_argv).

This module was split into a package (2026-06-04, pure structural move, no logic
change). The original namespace is preserved by re-exporting every symbol here;
external code keeps importing ``from codev_platform.ops.reindex import X``.
"""
from __future__ import annotations

from codev_platform.ops import _common as C

from .logs import (
    _append_log,
    _git_out,
    _now,
    _reindex_log,
)
from .proc import (
    _finish_log,
    _run_health_refresh,
    _run_logged_foreground,
    _spawn_background,
    _status_label,
)
from .dispatch import (
    _AUTO_REINDEX_RETIRED,
    _dispatch_reindex,
    auto_reindex_kinds,
    classify_scopes,
)
from .commands import (
    _FINISHED_RE,
    cmd_dirty_check,
    cmd_post_checkout,
    cmd_post_commit,
    cmd_post_merge,
    cmd_reindex,
    cmd_wait_for_reindex,
    register,
)

__all__ = [
    "C",
    "_AUTO_REINDEX_RETIRED",
    "_FINISHED_RE",
    "_append_log",
    "_dispatch_reindex",
    "_finish_log",
    "_git_out",
    "_now",
    "_reindex_log",
    "_run_health_refresh",
    "_run_logged_foreground",
    "_spawn_background",
    "_status_label",
    "auto_reindex_kinds",
    "classify_scopes",
    "cmd_dirty_check",
    "cmd_post_checkout",
    "cmd_post_commit",
    "cmd_post_merge",
    "cmd_reindex",
    "cmd_wait_for_reindex",
    "register",
]
