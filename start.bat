@echo off
chcp 65001 >nul
title BOSS 直聘工作台
cd /d "%~dp0"

echo 正在检查端口 5000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
    echo 发现旧进程 PID %%a，正在关闭...
    taskkill /F /PID %%a >nul 2>&1
)

echo 正在启动 BOSS 直聘工作台...
echo 关闭此窗口将停止服务。
echo.
start "" http://127.0.0.1:5000
".venv\Scripts\python.exe" "webui\app.py"
pause
