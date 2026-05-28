@echo off
setlocal

REM Cross-layer KG MCP server launcher.
REM Now owned by codev-platform (platform ownership inversion 2026-05-28).
REM Machine-specific paths driven by ~/.codev-platform/config.json
REM (data.platform_data_dir etc.), not hardcoded here.

REM Repo root = this file's dir (tools\cross_link\) up two levels.
for %%I in ("%~dp0..\..") do set "REPO_ROOT=%%~fI"

REM Reuse codev-platform root venv (codev-platform package installed there).
set "VENV_PY=%REPO_ROOT%\.venv\Scripts\python.exe"

"%VENV_PY%" -m codev_platform.cross_link.server
