"""脚本侧稳定运行文件路径与健康退出语义回归测试。"""

from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]


def test_ai_health从统一数据根读取召回日志() -> None:
    source = (_ROOT / "scripts" / "ai-health.ps1").read_text(encoding="utf-8")

    assert "Join-Path $PlatformDataDir 'logs\\search_recall.jsonl'" in source
    assert "codev_platform\\chroma\\search_recall.jsonl" not in source
    assert "[System.IO.Path]::IsPathRooted($Value)" in source
    assert "Resolve-PlatformDataPath $env:PLATFORM_DATA_DIR" in source


def test_update_local_ai保留健康降级退出码() -> None:
    source = (_ROOT / "scripts" / "update-local-ai.ps1").read_text(encoding="utf-8")

    assert "$rc = $LASTEXITCODE" in source
    assert "$rc = if ($healthRc -eq 2) { 0 } else { $healthRc }" not in source
