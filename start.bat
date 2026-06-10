@echo off
setlocal
set ROOT=%~dp0
cd /d "%ROOT%"

:: ── Backend setup ─────────────────────────────────────────────────────────────
if not exist "%ROOT%venv" (
    echo Creating virtual environment...
    python -m venv "%ROOT%venv"
)

call "%ROOT%venv\Scripts\activate.bat"
pip install -q -r "%ROOT%requirements.txt"

:: ── Frontend setup ────────────────────────────────────────────────────────────
if not exist "%ROOT%frontend\node_modules" (
    echo Installing frontend dependencies...
    npm install --prefix "%ROOT%frontend"
)

:: ── Start both servers ────────────────────────────────────────────────────────
echo.
echo Starting TriageIQ...
echo   Backend  ^-^> http://localhost:8001
echo   Frontend ^-^> http://localhost:3001
echo.
echo Close this window to stop both servers.
echo.

start "TriageIQ Backend" cmd /k "cd /d "%ROOT%" && call venv\Scripts\activate.bat && uvicorn backend.main:app --port 8001"
start "TriageIQ Frontend" cmd /k "npm run dev --prefix "%ROOT%frontend""

pause
