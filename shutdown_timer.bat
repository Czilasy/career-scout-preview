@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title 定时关机

:ask
set "MINUTES="
set /p "MINUTES=请输入多少分钟后关机（0 取消已设置的关机）: "
if not defined MINUTES goto ask

echo(!MINUTES!| findstr /r "^[0-9][0-9]*$" >nul || goto invalid

set /a "SECONDS=MINUTES*60"
if %SECONDS% LSS 0 goto invalid
if %SECONDS% EQU 0 goto cancel

shutdown /s /t %SECONDS% /c "已计划在 %MINUTES% 分钟后自动关机"
if errorlevel 1 (
    echo 设置失败。
    pause
    exit /b 1
)

echo.
echo 已设置：%MINUTES% 分钟后自动关机。
echo 想取消的话，再次打开本脚本并输入 0。
pause
exit /b 0

:cancel
shutdown /a >nul 2>nul
echo 已取消已设置的自动关机（如果之前没有设置，则忽略）。
pause
exit /b 0

:invalid
echo 输入无效，请输入整数分钟，例如 100。
pause
exit /b 1
