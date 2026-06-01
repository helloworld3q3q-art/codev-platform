"""audit-0601 #2: rules/skills 真值源 relocate 进 codev_platform/resources/ 后的可达性。

不真建 wheel(重 + 需网络);而是验证 relocate 的两个不变量, 它们等价于"wheel 能带走 + sync 找得到":
  1. importlib.resources 能定位 codev_platform/resources/{rules,skills}(= 进了包, package-data 会随 wheel 走);
  2. cli 的 _RULES_SRC / _SKILLS_SRC 解析到真实存在的目录(= sync-rules/sync-skills 有源)。
"""
from __future__ import annotations

from importlib.resources import files


def test_resources_rules_accessible_via_importlib():
    p = files("codev_platform") / "resources" / "rules"
    assert p.is_dir(), "codev_platform/resources/rules 应可经 importlib.resources 定位(wheel 才带得走)"
    mds = [c.name for c in p.iterdir() if c.name.endswith(".md")]
    assert mds, "resources/rules 下应有 .md 真值源规则"


def test_resources_skills_accessible_via_importlib():
    p = files("codev_platform") / "resources" / "skills"
    assert p.is_dir(), "codev_platform/resources/skills 应可经 importlib.resources 定位"
    subdirs = [c.name for c in p.iterdir() if c.is_dir()]
    assert subdirs, "resources/skills 下应有 skill 子目录"


def test_cli_sync_sources_resolve_to_existing_dirs():
    from codev_platform import cli
    assert cli._RULES_SRC.is_dir(), f"_RULES_SRC 应存在: {cli._RULES_SRC}"
    assert cli._SKILLS_SRC.is_dir(), f"_SKILLS_SRC 应存在: {cli._SKILLS_SRC}"
    # 解析结果应落在包内 resources(relocate 后的位置), 而非仓根旧布局
    assert "resources" in str(cli._RULES_SRC), "应解析到包内 resources/ 而非仓根 rules/"
