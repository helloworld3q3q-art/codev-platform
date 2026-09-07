"""codev_platform multi-project namespace 集成 smoke test。

可直接跑: python -m codev_platform.core._smoke_test
"""
from __future__ import annotations

import sys

from codev_platform.core.project_id import resolve_local
from codev_platform.core.paths import (
    chroma_collection_name,
    chroma_dir,
    codegraph_db_path,
)


def main() -> int:
    pid = resolve_local()
    print("=== multi-project namespace smoke test ===")
    print(f"project_id resolved: {pid}")
    print(f"chroma collection:   {chroma_collection_name(pid, 'platform_docs')}")
    print(f"chroma data dir:     {chroma_dir()}")
    print(f"codegraph db:        {codegraph_db_path(pid)} (per-repo via 3rd-party MCP server)")
    print()
    print("OK - codev_platform core resolves correctly")
    print("(chroma daemon 集成验证仍在 platform 仓 tools/_platform/_smoke_test.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
