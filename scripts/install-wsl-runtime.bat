@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

where wsl.exe >nul 2>nul
if errorlevel 1 (
  echo 未检测到 WSL，请先安装并启动 WSL。
  exit /b 1
)

set "SERVER_SCRIPT_WIN=%~dp0install-wsl-runtime.sh"
if not exist "%SERVER_SCRIPT_WIN%" (
  echo 未找到服务器安装脚本："%SERVER_SCRIPT_WIN%"
  exit /b 2
)

for /f "usebackq delims=" %%I in (`wsl.exe -d Ubuntu -- wslpath -a "%SERVER_SCRIPT_WIN%"`) do set "SERVER_SCRIPT_WSL=%%I"
if not defined SERVER_SCRIPT_WSL (
  echo 无法把安装脚本路径转换为 WSL 路径。
  exit /b 3
)

set "SHOW_RESULT=1"
:scan_args
if "%~1"=="" goto run_installer
if /i "%~1"=="--help" set "SHOW_RESULT=0"
if /i "%~1"=="-h" set "SHOW_RESULT=0"
shift
goto scan_args

:run_installer
wsl.exe -d Ubuntu -- bash "%SERVER_SCRIPT_WSL%" %*
set "EXIT_CODE=%ERRORLEVEL%"
if "%SHOW_RESULT%"=="0" exit /b %EXIT_CODE%
echo.
if not "%EXIT_CODE%"=="0" echo 安装失败，退出码：%EXIT_CODE%。
if "%EXIT_CODE%"=="0" echo 安装完成。
pause
exit /b %EXIT_CODE%
