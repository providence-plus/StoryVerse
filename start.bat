@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo   StoryVerse - local development mode
echo   ---------------------------------------------
echo.

if not exist "backend\.venv\Scripts\python.exe" (
  echo [1/4] Creating Python virtual environment...
  py -3 -m venv backend\.venv
  if errorlevel 1 python -m venv backend\.venv
)

echo [2/4] Checking backend dependencies...
"backend\.venv\Scripts\python.exe" -m pip install -q -r backend\requirements.txt
if errorlevel 1 (
  echo ERROR: backend dependency install failed, see messages above.
  pause
  exit /b 1
)

if not exist "frontend\node_modules" (
  echo [3/4] Installing frontend dependencies, first run may take a few minutes...
  pushd frontend
  call npm install
  if errorlevel 1 (
    popd
    echo ERROR: npm install failed, see messages above.
    pause
    exit /b 1
  )
  popd
)

echo [4/4] Starting backend 127.0.0.1:8000 and frontend 127.0.0.1:5173
echo        Open http://127.0.0.1:5173
echo        Close both windows to stop the app.
echo.

start "StoryVerse Backend" cmd /k "backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000"

pushd frontend
call npm run dev
popd
endlocal
