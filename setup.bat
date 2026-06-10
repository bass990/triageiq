@echo off
setlocal
set ROOT=%~dp0
cd /d "%ROOT%"

echo ============================================================
echo  TriageIQ Setup
echo ============================================================
echo.

:: ── Python venv ───────────────────────────────────────────────
echo [1/4] Removing old virtual environment (if any)...
if exist "%ROOT%venv" (
    rmdir /s /q "%ROOT%venv"
)

echo [2/4] Creating fresh virtual environment...
python -m venv "%ROOT%venv"
if errorlevel 1 (
    echo ERROR: Python not found or venv creation failed.
    echo Make sure Python is installed and on PATH.
    pause
    exit /b 1
)

call "%ROOT%venv\Scripts\activate.bat"

echo [3/4] Installing Python dependencies...
pip install -q -r "%ROOT%requirements.txt"
if errorlevel 1 (
    echo ERROR: pip install failed.
    pause
    exit /b 1
)

:: ── Node / Frontend ───────────────────────────────────────────
echo [4/4] Installing frontend dependencies...
if exist "%ROOT%frontend\node_modules" (
    rmdir /s /q "%ROOT%frontend\node_modules"
)

npm install --prefix "%ROOT%frontend"
if errorlevel 1 (
    echo ERROR: npm install failed.
    echo Make sure Node.js is installed and on PATH.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Setup complete! Run start.bat to launch TriageIQ.
echo ============================================================
echo.
pause
