@echo off
setlocal

REM platform-docs MCP entry (called by .mcp.json).
REM
REM Now owned by codev-platform (platform ownership inversion 2026-05-28).
REM venv lives at codev-platform repo root (.venv), built via:
REM   pip install -r requirements-runtime.txt ; pip install -e .
REM
REM Machine-specific paths (model dirs / GPU device / search top-K / data dir)
REM live in ~/.codev-platform/config.json. Env vars still override when defined.

REM Repo root = this file's dir (tools\chroma\) up two levels.
for %%I in ("%~dp0..\..") do set "REPO_ROOT=%%~fI"
set "VENV_PY=%REPO_ROOT%\.venv\Scripts\python.exe"

REM Daemon mode (multi-session GPU sharing). Config drives default true.
if /i "%PLATFORM_DOCS_DAEMON_MODE%"=="false" (
  "%VENV_PY%" -m codev_platform.chroma.server
) else (
  "%VENV_PY%" -m codev_platform.chroma.launcher
)
