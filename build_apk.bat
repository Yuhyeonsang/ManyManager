@echo off
REM ============================================================
REM EAS Build - .apk 파일을 클라우드에서 빌드해서 다운로드 링크 발급
REM 처음 실행 시: Expo 계정 로그인 / eas init 한 번만 진행
REM ============================================================
chcp 65001 > nul
cd /d "%~dp0fund-manager-app"

echo.
echo ===============================================
echo   FundManager .apk 빌드 시작
echo ===============================================
echo.
echo  [중요] 빌드 전 확인사항:
echo    1) src\services\api.js 의 BASE_URL 이
echo       클라우드/ngrok 주소인지 확인하세요.
echo       (192.168.x.x 면 집 와이파이에서만 작동합니다)
echo.
echo    2) 처음이면 자동으로 Expo 로그인 화면이 뜹니다.
echo       (계정 없으면 https://expo.dev/signup 에서 무료 가입)
echo.
echo  -^> 계속하려면 아무 키나, 취소하려면 창 닫기
pause > nul

REM EAS CLI 설치 (없으면)
where eas >nul 2>&1
if errorlevel 1 (
    echo [+] EAS CLI 설치 중...
    call npm install -g eas-cli
    if errorlevel 1 (
        echo [X] EAS CLI 설치 실패. Node.js 가 설치되어 있는지 확인하세요.
        pause
        exit /b 1
    )
)

REM 로그인 상태 확인
call eas whoami >nul 2>&1
if errorlevel 1 (
    echo [+] Expo 로그인 필요...
    call eas login
)

REM 프로젝트 EAS 초기화 (이미 되어 있으면 skip)
findstr /c:"\"projectId\"" app.json >nul 2>&1
if errorlevel 1 (
    echo [+] EAS 프로젝트 초기화...
    call eas init --non-interactive
)

REM 의존성 점검
if not exist "node_modules" (
    echo [+] npm install 진행 중...
    call npm install
)

echo.
echo ===============================================
echo   클라우드에서 .apk 빌드 (10~15분 소요)
echo ===============================================
echo.

call eas build --platform android --profile preview

echo.
echo ===============================================
echo  빌드가 완료되면 콘솔에 .apk 다운로드 링크가
echo  출력됩니다. 갤럭시 S24의 카카오톡/문자로 자기
echo  자신에게 보내거나, 핸드폰 브라우저에서 직접
echo  접속해서 다운로드하면 설치할 수 있습니다.
echo.
echo  웹 대시보드: https://expo.dev/accounts/[본인]/projects
echo ===============================================
pause
