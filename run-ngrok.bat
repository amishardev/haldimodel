@echo off
REM ==========================================================================
REM  Haldi ngrok tunnel launcher
REM  Run AFTER run-backend.bat is already running on port 8000.
REM ==========================================================================
setlocal

cd /d "%~dp0"

REM --- Auth (one-time, sets up your permanent domain) ----------------------
ngrok config add-authtoken 3GsHskT7vzTcFXahsckd0rb3CQj_6EJfGvkKbCGoeDaP2krrt

REM --- Check backend is up --------------------------------------------------
echo [check] Waiting for backend on http://localhost:8000/health ...
:wait_loop
curl -s -o NUL -w "%%{http_code}" http://localhost:8000/health | findstr "200" >NUL 2>&1
if errorlevel 1 (
    echo [check] Backend not ready yet, retrying in 2s...
    timeout /t 2 /nobreak >NUL
    goto wait_loop
)
echo [check] Backend is UP!
echo.

REM --- Start tunnel on permanent domain -----------------------------------
ngrok http --domain=jackpot-shuffle-boxlike.ngrok-free.dev 8000

pause
