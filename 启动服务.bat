@echo off
rem ============================================================
rem  掼蛋 Guandan — 一键启动 Web 服务
rem  双击运行：启动服务器并自动打开浏览器。
rem  若 8765 被占用会自动换下一个空闲端口。
rem  关闭本窗口即停止服务。
rem ============================================================
setlocal
cd /d "%~dp0"

set PYTHONIOENCODING=utf-8

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 python，请先安装 Python 3.11+ 并加入 PATH。
  pause
  exit /b 1
)

python -c "import flask" >nul 2>nul
if errorlevel 1 (
  echo 首次运行：安装依赖 requirements.txt ...
  pip install -r requirements.txt
)

python -m guandan.ui.web.app

pause

endlocal
