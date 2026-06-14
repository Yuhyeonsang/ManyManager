@echo off
cd /d "%~dp0"
echo [1] lock 파일 삭제 중...
del /f ".git\index.lock" 2>nul
del /f ".git\HEAD.lock" 2>nul
echo [2] git add
git add data_collector.py main.py
echo [3] git commit
git commit -m "ETF: 코드 수정(396500=TIGER반도체TOP10), 신규ETF 추가, 토큰검색, fund_name 보정, 구성종목뉴스"
echo [4] git push
git push
echo.
echo ===== 완료 =====
pause
