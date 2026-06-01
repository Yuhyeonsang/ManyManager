@echo off
REM ============================================================
REM 빠진 패키지 빠르게 추가 (react-native-gesture-handler 등)
REM   - node_modules 통째로 안 지우고 누락된 것만 추가
REM   - 1~2분 안에 끝남
REM ============================================================
chcp 949 > nul
cd /d "%~dp0\fund-manager-app"

echo.
echo ===============================================
echo   누락 패키지 빠른 추가
echo ===============================================
echo.
echo [1/2] react-native-gesture-handler 설치 중...
call npm install react-native-gesture-handler@~2.28.0 --legacy-peer-deps
if errorlevel 1 (
    echo  [X] 설치 실패
    pause
    exit /b 1
)
echo  [OK] 설치 완료

echo.
echo [2/2] expo install --fix 로 다른 패키지도 점검 중...
call npx expo install --fix
echo  [OK] 점검 완료

echo.
echo ===============================================
echo   [완료] 이제 run_app.bat 다시 실행하세요!
echo ===============================================
echo.
pause
