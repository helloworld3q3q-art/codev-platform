"""分发规则必须保持架构中立,不能夹带单个业务仓画像。"""
from __future__ import annotations

from importlib.resources import files


FORBIDDEN_TERMS = (
    "stock-admin",
    "apps/stock",
    "python/stock-pipeline",
    "Flyway",
    "DataFetcherEnum",
    "useModel",
    "pnpm run api",
    "pnpm run enums",
    "cross-layer-enum-consistency",
    "frontend-backend-handoff",
    "not-null-write-guard",
    "shadow-isolation",
    "PIT",
    "A 股",
    "量化",
    "StockRecommendation",
)


def test_distributed_rules_do_not_embed_project_specific_profile():
    root = files("codev_platform") / "resources" / "rules"
    offenders: list[str] = []
    for item in root.iterdir():
        if item.name == "README.md" or not item.name.endswith(".md"):
            continue
        text = item.read_text(encoding="utf-8")
        for term in FORBIDDEN_TERMS:
            if term in text:
                offenders.append(f"{item.name}: {term}")

    assert not offenders, "分发规则含项目专属画像: " + ", ".join(offenders)
