@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo   StoryVerse - trusted LAN mode
echo   ---------------------------------------------
echo   Use ONLY on a trusted home Wi-Fi.
echo   Do NOT expose these ports to the public internet.
echo.

if not exist "backend\.venv\Scripts\python.exe" (
  echo [1/4] Creating Python virtual environment...
  py -3 -m venv backend\.venv
  if errorlevel 1 python -m venv backend\.venv
)

echo [2/4] Checking backend dependencies...
"backend\.venv\Scripts\python.exe" -m pip install -q -r backend\requirements.txt
if errorlevel 1 (
  echo ERROR: backend dependency install failed.
  pause
  exit /b 1
)

if not exist "frontend\node_modules" (
  echo [3/4] Installing frontend dependencies...
  pushd frontend
  call npm install
  if errorlevel 1 (
    popd
    echo ERROR: npm install failed.
    pause
    exit /b 1
  )
  popd
)

echo [4/4] Starting. Backend stays on 127.0.0.1; only the Vite dev server listens on LAN.
echo        Run "ipconfig" and find your IPv4 address, then on your phone open:
echo        http://YOUR-PC-IP:5173
echo.

start "StoryVerse Backend" cmd /k "backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000"

pushd frontend
call npm run dev:lan
popd
endlocal
