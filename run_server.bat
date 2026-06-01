@echo off
REM ============================================================
REM FastAPI 서버 실행 (모바일 앱이 호출하는 백엔드)
REM ============================================================
chcp 949 > nul
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [X] .venv 가 없습니다. 먼저 setup.bat 을 실행하세요.
    pause
    exit /b 1
)

if not exist ".env" (
    echo [!] .env 파일이 없습니다. .env.example 을 .env 로 복사해서 API 키를 채우세요.
    pause
    exit /b 1
)

echo.
echo ===============================================
echo   FastAPI 서버 시작 - http://0.0.0.0:8000
echo ===============================================
echo.
echo  PC IP 주소 확인:
ipconfig | findstr /i "IPv4"
echo.
echo  -^> 위 IP 주소를 fund-manager-app\src\services\api.js 의
echo     BASE_URL 에 적어두세요. (예: http://192.168.0.10:8000)
echo.
echo  -^> 종료하려면 Ctrl+C
echo ===============================================
echo.

call .venv\Scripts\activate.bat
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

pause
