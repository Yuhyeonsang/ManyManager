# ============================================================
# 펀드매니저 - 풀 자동 설치 (다른 PC 이식용)
# ============================================================
#  이 스크립트가 하는 일:
#    1) winget으로 Python 3.12 + Node.js LTS 자동 설치
#       (winget이 없으면 설치 페이지를 띄움)
#    2) Python 가상환경 생성 + requirements.txt 설치
#    3) .env.example -> .env 자동 복사
#    4) fund-manager-app/ 에서 npm install
#    5) PC IP 주소를 자동으로 api.js에 적용
#
#  실행 방법: install_all.bat 더블클릭
# ============================================================

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

function Write-Step { param([string]$msg) Write-Host "`n[$([DateTime]::Now.ToString('HH:mm:ss'))] $msg" -ForegroundColor Cyan }
function Write-OK   { param([string]$msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn { param([string]$msg) Write-Host "  [!]  $msg" -ForegroundColor Yellow }
function Write-Err  { param([string]$msg) Write-Host "  [X]  $msg" -ForegroundColor Red }

Write-Host @"
================================================================
  FundManager 풀 자동 설치 (다른 PC 이식용)
  Python + Node.js + Python 패키지 + npm 패키지 일괄 설치
================================================================
"@ -ForegroundColor White

# ---------------- 1. Python 설치 ----------------
Write-Step "Python 3.12 확인/설치"
$pythonOK = $false
try {
    $v = & python --version 2>&1
    if ($LASTEXITCODE -eq 0) { Write-OK "이미 설치됨: $v"; $pythonOK = $true }
} catch {}

if (-not $pythonOK) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "  -> winget으로 Python 3.12 설치 중..." -ForegroundColor Yellow
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        Write-OK "Python 설치 완료 (재시작이 필요할 수 있음)"
    } else {
        Write-Err "winget이 없습니다. 수동 설치 필요"
        Write-Host "  -> https://www.python.org/downloads/ 에서 다운로드"
        Write-Host "  -> 설치 시 [Add python.exe to PATH] 반드시 체크"
        Start-Process "https://www.python.org/downloads/"
        Read-Host "설치 끝나면 Enter"
    }
}

# ---------------- 2. Node.js 설치 ----------------
Write-Step "Node.js LTS 확인/설치"
$nodeOK = $false
try {
    $v = & node --version 2>&1
    if ($LASTEXITCODE -eq 0) { Write-OK "이미 설치됨: $v"; $nodeOK = $true }
} catch {}

if (-not $nodeOK) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "  -> winget으로 Node.js LTS 설치 중..." -ForegroundColor Yellow
        winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        Write-OK "Node.js 설치 완료 (재시작이 필요할 수 있음)"
    } else {
        Write-Err "winget이 없습니다. 수동 설치 필요"
        Write-Host "  -> https://nodejs.org 에서 LTS 다운로드"
        Start-Process "https://nodejs.org/"
        Read-Host "설치 끝나면 Enter"
    }
}

# ---------------- 3. Python venv + 패키지 ----------------
Write-Step "Python 가상환경(.venv) 생성"
if (-not (Test-Path ".venv")) {
    & python -m venv .venv
    Write-OK ".venv 생성됨"
} else {
    Write-OK ".venv 이미 존재"
}

Write-Step "Python 패키지 설치 (requirements.txt)"
& ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
& ".venv\Scripts\pip.exe" install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Err "pip install 실패"; exit 1 }
Write-OK "Python 패키지 설치 완료"

# ---------------- 4. .env 준비 ----------------
Write-Step ".env 파일 준비"
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-OK ".env 파일 생성됨"
    Write-Warn "메모장으로 .env 열어서 API 키 3개 채워 넣으세요"
} else {
    Write-OK ".env 이미 존재 - 보존"
}

# ---------------- 5. npm install ----------------
Write-Step "Expo 앱 npm install"
Push-Location "fund-manager-app"
if (-not (Test-Path "node_modules")) {
    & npm install
    if ($LASTEXITCODE -ne 0) { Write-Err "npm install 실패"; Pop-Location; exit 1 }
    Write-OK "npm install 완료"
} else {
    Write-OK "node_modules 이미 존재"
}
Pop-Location

# ---------------- 6. PC IP 자동 검출 + api.js 패치 ----------------
Write-Step "PC IP 주소 자동 검출 + api.js 자동 설정"
$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
       Where-Object { $_.PrefixOrigin -ne "WellKnown" -and $_.IPAddress -notlike "169.*" -and $_.IPAddress -ne "127.0.0.1" } |
       Select-Object -First 1).IPAddress

if ($ip) {
    Write-OK "감지된 PC IP: $ip"
    $apiFile = "fund-manager-app\src\services\api.js"
    if (Test-Path $apiFile) {
        $content = Get-Content $apiFile -Raw
        $newContent = $content -replace "BASE_URL\s*=\s*['""]http://[^'""]+['""]", "BASE_URL = 'http://$ip`:8000'"
        if ($newContent -ne $content) {
            Set-Content $apiFile $newContent -NoNewline
            Write-OK "api.js의 BASE_URL을 http://$ip`:8000 으로 자동 설정"
        } else {
            Write-Warn "api.js의 BASE_URL 패턴을 못 찾음 - 수동 수정 필요"
        }
    } else {
        Write-Warn "$apiFile 이 없음 - 나중에 직접 수정"
    }
} else {
    Write-Warn "IP 자동 검출 실패 - ipconfig 로 IPv4 확인 후 직접 입력"
}

# ---------------- 완료 ----------------
Write-Host @"

================================================================
  [완료] 모든 설치 끝!
================================================================
다음 순서로 실행:
  1) 메모장으로 .env 파일 열어 API 키 3개 채우기
       - GEMINI_API_KEY     (https://aistudio.google.com/apikey)
       - NAVER_CLIENT_ID/SECRET (https://developers.naver.com/apps)
       - DART_API_KEY       (https://opendart.fss.or.kr)
  2) 갤럭시 S24에 'Expo Go' 앱 설치
  3) PC와 갤럭시를 같은 와이파이에 연결
  4) run_server.bat 더블클릭   - FastAPI 백엔드 실행
  5) run_app.bat    더블클릭   - Expo 개발 서버 실행
  6) Expo Go 앱으로 QR 스캔

자세한 가이드:  START_HERE.md
================================================================
"@ -ForegroundColor Green

Read-Host "Enter를 누르면 종료"
