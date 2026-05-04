@echo off
REM ============================================================
REM Expo 앱 모듈 오류 복구 스크립트 (SDK 54 기준)
REM   - node_modules 와 package-lock.json 완전 삭제
REM   - npm 캐시 청소
REM   - 새로 npm install
REM   - npx expo install --fix 로 모든 패키지 버전 SDK에 맞게 자동 정합
REM ============================================================
chcp 949 > nul
cd /d "%~dp0\fund-manager-app"

echo.
echo ===============================================
echo   Expo 앱 클린 재설치 (SDK 54)
echo ===============================================
echo.

if exist "node_modules" (
    echo [1/5] node_modules 폴더 삭제 중... (1~2분 소요)
    rmdir /s /q "node_modules"
    echo  [OK] 삭제 완료
) else (
    echo [1/5] node_modules 없음 - 건너뜀
)

if exist "package-lock.json" (
    echo [2/5] package-lock.json 삭제 중...
    del /q "package-lock.json"
    echo  [OK] 삭제 완료
) else (
    echo [2/5] package-lock.json 없음 - 건너뜀
)

echo.
echo [3/5] npm 캐시 청소 중...
call npm cache clean --force
echo  [OK] 캐시 청소 완료

echo.
echo [4/5] npm install 재실행 중... (5~10분 소요, 인내심 필요)
echo       Expo SDK 54 + React Native 0.81 설치
call npm install --legacy-peer-deps
if errorlevel 1 (
    echo.
    echo  [X] npm install 실패 - 인터넷 연결 또는 디스크 공간 확인
    pause
    exit /b 1
)
echo  [OK] npm install 완료

echo.
echo [5/5] expo install --fix 로 패키지 버전 자동 정합 중...
call npx expo install --fix
echo  [OK] 패키지 버전 정합 완료

echo.
echo ===============================================
echo   [완료] 클린 재설치 끝!
echo ===============================================
echo.
echo  이제 run_app.bat 을 다시 실행하세요.
echo  핸드폰 Expo Go 앱이 SDK 54를 지원해야 합니다.
echo.
pause
