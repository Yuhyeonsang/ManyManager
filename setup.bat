@echo off
REM ============================================================
REM FundManager - 원클릭 환경 설치 스크립트 (Windows)
REM ============================================================
setlocal enabledelayedexpansion
chcp 949 > nul
cd /d "%~dp0"

echo.
echo ===============================================
echo   FundManager 자동 환경 설치 시작
echo ===============================================
echo.

REM ---------- 1. Python 설치 확인 ----------
echo [1/5] Python 설치 확인 중...
where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo  [X] Python이 설치되어 있지 않습니다.
    echo.
    echo  설치 방법:
    echo    1^) https://www.python.org/downloads/ 접속
    echo    2^) "Download Python 3.12.x" 클릭
    echo    3^) 설치 화면에서 [Add python.exe to PATH] 반드시 체크
    echo    4^) Install Now 클릭
    echo    5^) 설치 완료 후 이 setup.bat을 다시 실행
    echo.
    pause
    start https://www.python.org/downloads/
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo  [OK] Python !PYVER! 감지됨

REM ---------- 2. Node.js 설치 확인 ----------
echo.
echo [2/5] Node.js 설치 확인 중...
where node >nul 2>nul
if errorlevel 1 (
    echo.
    echo  [X] Node.js가 설치되어 있지 않습니다.
    echo.
    echo  설치 방법:
    echo    1^) https://nodejs.org 접속
    echo    2^) [LTS] 버튼 클릭 (왼쪽 큰 버튼)
    echo    3^) 다운로드한 .msi 실행 (모든 옵션 기본값)
    echo    4^) 설치 완료 후 이 setup.bat을 다시 실행
    echo.
    pause
    start https://nodejs.org/
    exit /b 1
)
for /f %%i in ('node --version 2^>^&1') do set NODEVER=%%i
echo  [OK] Node.js !NODEVER! 감지됨

REM ---------- 3. Python 가상환경 + 패키지 ----------
echo.
echo [3/5] Python 가상환경 생성 중...
if not exist ".venv" (
    python -m venv .venv
    if errorlevel 1 (
        echo  [X] 가상환경 생성 실패
        pause
        exit /b 1
    )
    echo  [OK] .venv 생성됨
) else (
    echo  [OK] .venv 이미 존재 - 건너뜀
)

echo.
echo  Python 패키지 설치 중... (1~3분 소요)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt
if errorlevel 1 (
    echo  [X] 패키지 설치 실패 - 인터넷 연결 확인
    pause
    exit /b 1
)
echo  [OK] Python 패키지 설치 완료

REM ---------- 4. .env 파일 준비 ----------
echo.
echo [4/5] .env 파일 준비 중...
if not exist ".env" (
    copy ".env.example" ".env" > nul
    echo  [OK] .env 파일이 .env.example로부터 복사되었습니다
    echo  [!] .env 파일을 메모장으로 열어서 API 키 3개를 채워 넣으세요:
    echo      - GEMINI_API_KEY      (https://aistudio.google.com/apikey)
    echo      - NAVER_CLIENT_ID/SECRET (https://developers.naver.com/apps)
    echo      - DART_API_KEY         (https://opendart.fss.or.kr)
) else (
    echo  [OK] .env 이미 존재 - 보존
)

REM ---------- 5. Expo 앱 npm install ----------
echo.
echo [5/5] 모바일 앱 패키지 설치 중... (3~5분 소요)
cd fund-manager-app
if not exist "node_modules" (
    call npm install
    if errorlevel 1 (
        echo  [X] npm install 실패
        cd ..
        pause
        exit /b 1
    )
    echo  [OK] 모바일 앱 패키지 설치 완료
) else (
    echo  [OK] node_modules 이미 존재 - 건너뜀
)
cd ..

REM ---------- 완료 ----------
echo.
echo ===============================================
echo   [완료] 환경 설치 끝!
echo ===============================================
echo.
echo  다음 단계:
echo    1^) .env 파일을 메모장으로 열어서 API 키 채워넣기
echo    2^) 갤럭시 S24에 "Expo Go" 앱 설치
echo    3^) PC와 갤럭시를 같은 와이파이에 연결
echo    4^) ipconfig 로 PC IP 확인 후
echo       fund-manager-app\src\services\api.js 의 BASE_URL 수정
echo    5^) run_server.bat 더블클릭 - FastAPI 서버 실행
echo    6^) run_app.bat    더블클릭 - Expo 개발 서버 실행
echo    7^) Expo Go 앱으로 QR 스캔
echo.
echo  자세한 내용은 START_HERE.md 파일을 보세요
echo.
pause
