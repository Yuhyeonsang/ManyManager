@echo off
chcp 65001 > nul
cd /d "%~dp0"

if exist ".git\index.lock" del ".git\index.lock"

set MSG=%~1
if "%MSG%"=="" set MSG=update

echo.
echo === changes ===
git status --short
echo.

git add -A
git commit -m "%MSG%"
git push origin main

echo.
echo === done ===
pause
