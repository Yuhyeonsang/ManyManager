@echo off
REM ============================================================
REM Expo 개발 서버 실행 (갤럭시 S24에서 QR 스캔)
REM ============================================================
chcp 949 > nul
cd /d "%~dp0\fund-manager-app"

if not exist "node_modules" (
    echo [X] node_modules 가 없습니다. 먼저 setup.bat 을 실행하세요.
    pause
    exit /b 1
)

echo.
echo ===============================================
echo   Expo 개발 서버 시작
echo ===============================================
echo.
echo  사용법:
echo    1^) 갤럭시 S24에서 "Expo Go" 앱 실행
echo    2^) 터미널에 뜨는 QR 코드를 "Scan QR code" 로 스캔
echo    3^) 잠시 기다리면 앱이 자동 다운로드됨
echo.
echo  ※ 핸드폰과 PC가 [같은 와이파이] 에 연결되어 있어야 함
echo ===============================================
echo.

call npm start

pause
