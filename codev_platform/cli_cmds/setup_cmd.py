"""setup 子命令 (新机器一键接入) + 子进程 helper —— 从 cli.py 拆出。

复用 sync 模块的 _RULES_SRC / _SKILLS_SRC / _sync_dir (step 5/5 sync 到本仓)。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codev_platform.cli_cmds._shared import _eprint, _print
from codev_platform.cli_cmds.sync import _RULES_SRC, _SKILLS_SRC, _sync_dir


def _which(cmd: str) -> str | None:
    """跨平台查 PATH 里有没有该命令, 返回绝对路径或 None."""
    import shutil
    return shutil.which(cmd)


def _run_subprocess(cmd: list[str], cwd: Path | None = None, label: str = "") -> int:
    """跑子进程 + 实时打印, 返回 exit code."""
    import subprocess
    _print(f"  → {label or ' '.join(cmd)}")
    try:
        rc = subprocess.call(cmd, cwd=str(cwd) if cwd else None)
        return rc
    except FileNotFoundError as exc:
        _eprint(f"    FAIL: {exc!s}")
        return 127


def cmd_setup(args: argparse.Namespace) -> int:
    """一键新机器接入 (自包含布局, 跨平台): preflight + venv + 模型 detect + 写 config + MCP 接入指引 + sync.

    布局 (2026-05-28 所有权翻正后): venv / data / 模型 config 全围绕本仓
    (codev-platform/.venv, codev-platform/data), 无需兄弟仓即可起 chroma 检索。

    模型自备 (放 ~/models/Qwen3-*); 路径走 config + pathlib (零盘符字面量); 本命令探测 + 给下载命令, 不下重物。
    Mac/Linux: 打印 .mcp.json 接入指引 (CLI 不替你改 shell profile)。
    """
    from codev_platform.core.config import load_config, save_config, get
    import platform as _platform
    codev_root = Path(__file__).resolve().parents[2]
    is_win = sys.platform == "win32"
    is_mac = sys.platform == "darwin"
    missing: list[str] = []
    _print(f"codev-platform repo: {codev_root}")
    _print(f"platform:            {sys.platform} / {_platform.machine()}")
    _print()

    # step 0/5: preflight 外部命令
    _print("=== step 0/5: preflight 外部命令 ===")
    if _which("claude"):
        _print(f"  claude: OK ({_which('claude')})")
    else:
        _print("  claude: MISSING — 装 Anthropic Claude Code CLI")
        missing.append("claude")
    if args.auto and not _which("uv"):
        _print("  uv: MISSING (--auto 建 venv 时优先用; 缺则回退 python -m venv) — https://astral.sh/uv")
    _print()

    # step 1/5: 仓内 .venv (self-contained, 无需兄弟仓)
    venv_dir = codev_root / ".venv"
    venv_py = venv_dir / ("Scripts/python.exe" if is_win else "bin/python")
    _print("=== step 1/5: 仓内 .venv ===")
    if venv_py.is_file():
        _print(f"  found: {venv_dir}")
    elif args.auto:
        _print("  MISSING, auto-installing ...")
        if _which("uv"):
            _run_subprocess(["uv", "venv"], cwd=codev_root, label="uv venv")
            _run_subprocess(["uv", "pip", "install", "-e", ".[runtime]", "--python", str(venv_py)], cwd=codev_root, label="uv pip install -e .[runtime]")
        else:
            _run_subprocess([sys.executable, "-m", "venv", str(venv_dir)], label="python -m venv .venv")
            _run_subprocess([str(venv_py), "-m", "pip", "install", "-e", ".[runtime]"], cwd=codev_root, label="pip install -e .[runtime]")
    else:
        _print("  MISSING — 跑: python3 -m venv .venv && .venv/bin/pip install -e '.[runtime]'  (或 setup --auto)")
    if not venv_py.is_file():
        missing.append("venv")
    _print()

    # step 2/5: codev_platform 可在 venv import
    if venv_py.is_file():
        _print("=== step 2/5: codev_platform import 检查 ===")
        if _run_subprocess([str(venv_py), "-c", "import codev_platform"], label="import codev_platform") != 0:
            if args.auto:
                _run_subprocess([str(venv_py), "-m", "pip", "install", "-e", str(codev_root)], label="pip install -e codev-platform")
            else:
                _print(f"  未装 — 跑: {venv_py} -m pip install -e {codev_root}")
                missing.append("codev_platform-in-venv")
        else:
            _print("  OK")
        _print()

    # step 3/5: 模型 detect (config 驱动, 跨平台, 不下载) — 优先 config, 再 ~/models, 再仓内 fallback
    cfg_pre = load_config()
    model_root = Path.home() / "models"
    cfg_embed = get(cfg_pre, "models.embed_path")
    cfg_reranker = get(cfg_pre, "models.reranker_path")
    embed_path = next((str(m) for m in [
        Path(cfg_embed).expanduser() if cfg_embed else None,
        model_root / "Qwen3-Embedding-0.6B",
        codev_root / "models" / "paraphrase-multilingual-MiniLM-L12-v2",
    ] if m and m.is_dir()), None)
    reranker_path = next((str(r) for r in [
        Path(cfg_reranker).expanduser() if cfg_reranker else None,
        model_root / "Qwen3-Reranker-0.6B",
    ] if r and r.is_dir()), "")
    _print("=== step 3/5: 模型 (自备, 不下载) ===")
    if embed_path:
        _print(f"  embed:    {embed_path}")
    else:
        _print(f"  MISSING embed — 下: hf download Qwen/Qwen3-Embedding-0.6B --local-dir {model_root}/Qwen3-Embedding-0.6B")
        missing.append("embed-model")
    _print(f"  reranker: {reranker_path or 'MISSING (可选, 关则纯向量+BM25)'}")
    _print()

    # step 4/5: 写 config (device 按平台探测; data 走仓内默认 null)
    if is_mac:
        device = "mps" if _platform.machine() == "arm64" else "cpu"
    elif is_win:
        device = "cuda"  # NVIDIA 默认; 无 N 卡手动改 cpu
    else:
        device = "cpu"  # linux 默认; 有 N 卡手动改 cuda
    _print(f"=== step 4/5: 写 config (embed_device={device}) ===")
    cfg = load_config()
    m = cfg.setdefault("models", {})
    rt = cfg.setdefault("runtime", {})
    before = json.dumps({"models": dict(m), "runtime": dict(rt)}, ensure_ascii=False, sort_keys=True)
    if embed_path:
        m["embed_path"] = embed_path
    m["embed_device"] = device
    m["reranker_path"] = reranker_path
    m["reranker_enabled"] = bool(reranker_path)
    if venv_py.is_file():
        rt["chroma_venv"] = str(venv_dir)
    changed = json.dumps({"models": dict(m), "runtime": dict(rt)}, ensure_ascii=False, sort_keys=True) != before
    if changed and not args.dry_run:
        p = save_config(cfg)
        _print(f"  写入 {p}")
    elif changed:
        _print("  [dry-run] 会更新 models/runtime: " + json.dumps({"models": dict(m), "runtime": dict(rt)}, ensure_ascii=False))
    else:
        _print("  config 已 match, 无需更新")
    _print()

    # step 5/5: sync rules/skills 到本仓 + MCP 接入指引
    _print("=== step 5/5: sync rules/skills + MCP 接入 ===")
    if args.auto and not args.dry_run:
        for kind, src in [("rules", _RULES_SRC), ("skills", _SKILLS_SRC)]:
            dst = codev_root / ".claude" / kind
            if _sync_dir(src, dst, f"codev-platform/{kind}", dry_run=False) >= 0:
                _print(f"  .claude/{kind}: synced")
    else:
        _print("  (--auto 时自动 sync; 或手动 codev-platform sync-rules / sync-skills)")
    if is_win:
        _print("  Windows: .mcp.json 默认即 cmd /c daemon launcher, 无需额外 env。")
    elif is_mac:
        # .mcp.json 的 ${VAR} 展开取自 VSCode 扩展宿主 process.env。Dock/Finder 启动的 VSCode
        # 不读 ~/.zshrc(只继承 launchd 环境) → 必须用 launchctl(GUI 可见)+ LaunchAgent 持久化。
        import subprocess as _sp
        try:
            cur = _sp.check_output(["launchctl", "getenv", "PLATFORM_MCP_CHROMA"], text=True).strip()
        except Exception:  # noqa: BLE001
            cur = ""
        if cur:
            _print("  Mac MCP env: OK (launchctl 已设 PLATFORM_MCP_CHROMA)")
        else:
            _print("  Mac 接 Claude Code: 用 launchctl 设 4 个 env (别用 ~/.zshrc — Dock 启动的 VSCode 读不到)。")
            _print("  跑 docs/onboarding-mac.md 『接入 Claude Code』那段 (写 LaunchAgent + launchctl setenv), 再 Cmd+Q 重启 VSCode。")
    else:
        _print("  Linux 接 Claude Code: 在登录环境 export PLATFORM_MCP_SH=sh / FLAG=-c / CHROMA / CROSSLINK (见 docs/onboarding-mac.md), 或从终端启动 IDE。")
    _print()

    # 收尾
    if not missing:
        _print(">>> READY <<< venv + codev_platform + 模型 + config 齐备")
        if not is_win:
            _print("  最后: 建索引 (python -m codev_platform.chroma.indexer --force) + 配 MCP env (launchctl, 见 docs/onboarding-mac.md) + 重启 VSCode")
        return 0
    _print(f">>> INCOMPLETE <<< 缺: {', '.join(missing)}")
    _print("  按上面 MISSING 行补齐后, 再跑 codev-platform setup --auto")
    return 1
