@echo off
cd /d "%~dp0"
echo [1] lock 파일 삭제 중...
del /f ".git\index.lock" 2>nul
del /f ".git\HEAD.lock" 2>nul
echo [2] git add
git add data_collector.py main.py analyzer.py fund-manager-app/src/screens/DetailScreen.js
echo [3] git commit
git commit -m "ETF: 이름표시수정, 구성종목뉴스3개분리, ETF전용리포트, 관심종목이름매핑, 검색토큰매칭"
echo [4] git push
git push
echo.
echo ===== 완료 =====
pause
