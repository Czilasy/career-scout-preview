@echo off
chcp 65001 >nul
title Career Scout 工作台
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo 正在检查端口 5000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
    echo 发现旧进程 PID %%a，正在关闭...
    taskkill /F /PID %%a >nul 2>&1
)

echo 正在启动 Career Scout 工作台...
echo 关闭此窗口将停止服务。
echo.
start /b "" "%PY%" "webui\app.py"

set /a tries=0
:wait_loop
timeout /t 1 /nobreak >nul
netstat -ano | findstr ":5000" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto ready
set /a tries+=1
if %tries% lss 60 goto wait_loop
echo 服务 60 秒内未就绪，请查看上方错误信息。
pause
exit /b 1

:ready
start "" http://127.0.0.1:5000
echo 浏览器已就绪：http://127.0.0.1:5000
pause
