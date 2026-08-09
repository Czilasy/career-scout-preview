@echo off
setlocal
chcp 65001 >nul
title Career Scout 工作台
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

set "PORT=5000"

rem 运行冒烟可覆盖端口/解释器/等待次数，默认行为不变。
if defined CAREER_SCOUT_PORT set "PORT=%CAREER_SCOUT_PORT%"
if defined CAREER_SCOUT_PY set "PY=%CAREER_SCOUT_PY%"
if defined CAREER_SCOUT_WAIT_TRIES set "WAIT_TRIES=%CAREER_SCOUT_WAIT_TRIES%"
if not defined WAIT_TRIES set "WAIT_TRIES=60"
set "READY_URL=http://127.0.0.1:%PORT%/api/session"

echo 正在检查端口 %PORT% 与 Career Scout 旧进程...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$pattern='webui[\\/]app\.py|CareerScout';" ^
  "$listeners=@(Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique);" ^
  "$old=@();" ^
  "foreach ($procId in $listeners) { $proc=Get-CimInstance Win32_Process -Filter ('ProcessId='+$procId) -ErrorAction SilentlyContinue; if ($proc -and $proc.CommandLine -match $pattern) { $old += $procId } }" ^
  "$unrelated=@($listeners | Where-Object { $_ -notin $old });" ^
  "if ($old.Count -gt 0) { foreach ($procId in $old) { Write-Host ('关闭 Career Scout 旧进程 PID '+$procId); Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue } }" ^
  "if ($unrelated.Count -gt 0) { Write-Host '端口 %PORT% 已被其它程序占用，未关闭任何进程，请先释放端口后重试。'; exit 2 }" ^
  "exit 0"
if errorlevel 2 (
    echo 启动中止：端口 %PORT% 被其它程序占用。
    pause
    exit /b 2
)

echo 正在启动 Career Scout 工作台...
echo 关闭此窗口将停止服务。
echo.
start /b "" "%PY%" "webui\app.py"

set /a tries=0
:wait_loop
curl.exe -s -o nul --max-time 2 -w "%%{http_code}" "%READY_URL%" | findstr /x "200" >nul 2>&1
if not errorlevel 1 goto ready
set /a tries+=1
if %tries% lss %WAIT_TRIES% goto wait_loop

echo 服务在 %WAIT_TRIES% 秒内未就绪（%READY_URL%），请查看上方启动日志。
pause
exit /b 1

:ready
start "" http://127.0.0.1:%PORT%
echo 服务已就绪：http://127.0.0.1:%PORT%
pause
