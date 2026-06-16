@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist ".git\index.lock" del /f ".git\index.lock"

echo [1] git add...
git add data_collector.py main.py fund-manager-app/src/screens/FavoritesScreen.js fund-manager-app/src/services/database.js

echo [2] git commit...
git commit -m "fix: pykrx dynamic ETF + find_watch_entry + 487240 search + favorites name update"

echo [3] git push...
git push

echo.
echo === Done ===
echo Pi: git pull + uvicorn restart
echo Windows: cd fund-manager-app and eas update --branch main
echo.
pause
