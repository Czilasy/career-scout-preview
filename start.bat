@echo off
chcp 65001 >nul
title BOSS 直聘工作台
cd /d "%~dp0"
echo 正在启动 BOSS 直聘工作台...
echo 关闭此窗口将停止服务。
echo.
start "" http://127.0.0.1:5000
".venv\Scripts\python.exe" "webui\app.py"
pause
