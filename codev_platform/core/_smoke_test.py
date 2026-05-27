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
    cross_link_db_path,
    cross_link_legacy_db_path,
)


def main() -> int:
    pid = resolve_local()
    print("=== multi-project namespace smoke test ===")
    print(f"project_id resolved: {pid}")
    print(f"chroma collection:   {chroma_collection_name(pid, 'platform_docs')}")
    print(f"chroma data dir:     {chroma_dir()}")
    print(f"cross-link new:      {cross_link_db_path(pid)}")
    legacy = cross_link_legacy_db_path()
    print(f"cross-link legacy:   {legacy} (exists={legacy.exists()})")
    print(f"codegraph db:        {codegraph_db_path(pid)} (per-repo via 3rd-party MCP server)")
    print()

    # cross_link.schema 集成
    from cross_link.schema import DB_PATH, LEGACY_DB_PATH, PROJECT_ID as PID2
    print(f"cross_link.schema.PROJECT_ID: {PID2}")
    print(f"cross_link.schema.DB_PATH:    {DB_PATH}")
    print(f"  - exists: {DB_PATH.exists()}")
    print(f"cross_link.schema.LEGACY:     {LEGACY_DB_PATH}")
    print(f"  - exists: {LEGACY_DB_PATH.exists()}")
    print()
    print("OK - all imports + resolution work")
    return 0


if __name__ == "__main__":
    sys.exit(main())
