@echo off
REM ============================================================
REM 24시간 운영 스크립트
REM   - FastAPI 서버 (창1)
REM   - Cloudflare Tunnel (창2)  → 인터넷에서 접근 가능한 https URL 발급
REM   PC 부팅 시 자동 실행하려면 이 .bat 의 바로가기를
REM   "C:\Users\dbgus\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup"
REM   폴더에 넣으면 됩니다.
REM ============================================================
chcp 65001 > nul
cd /d "%~dp0"

REM cloudflared 설치 확인
where cloudflared >nul 2>&1
if errorlevel 1 (
    echo [X] cloudflared 가 설치되지 않았습니다.
    echo     PowerShell 관리자 권한으로 다음 명령 실행:
    echo       winget install --id Cloudflare.cloudflared
    pause
    exit /b 1
)

REM .venv 확인
if not exist ".venv\Scripts\activate.bat" (
    echo [X] .venv 가 없습니다. setup.bat 을 먼저 실행하세요.
    pause
    exit /b 1
)

echo.
echo ===============================================
echo   24x7 운영 시작
echo ===============================================
echo.
echo   창 1: FastAPI 서버 (포트 8000)
echo   창 2: Cloudflare Tunnel  ← 여기에 https URL 출력됨
echo.
echo   [중요] 창 2 의 출력 중 다음 줄을 찾으세요:
echo     ^|  https://xxxx-xxxx-xxxx.trycloudflare.com  ^|
echo.
echo   이 주소를 fund-manager-app\src\services\api.js 의
echo   BASE_URL 에 넣고 .apk 를 빌드하면 어디서나 작동합니다.
echo ===============================================
echo.

REM 창 1: FastAPI
start "FundManager - FastAPI" cmd /k "cd /d %~dp0 && call .venv\Scripts\activate.bat && uvicorn main:app --host 0.0.0.0 --port 8000"

REM 잠깐 대기 (서버가 켜질 때까지)
timeout /t 3 /nobreak > nul

REM 창 2: Cloudflare Tunnel
start "FundManager - Cloudflare Tunnel" cmd /k "cloudflared tunnel --url http://localhost:8000"

echo  두 창이 열렸습니다. 이 창은 닫아도 됩니다.
timeout /t 5 /nobreak > nul
exit /b 0
