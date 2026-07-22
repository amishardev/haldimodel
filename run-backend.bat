@echo off
REM ==========================================================================
REM  Haldi backend launcher for WINDOWS SERVER
REM  Double-click this, or run it from cmd. Creates the venv on first run.
REM ==========================================================================
setlocal

cd /d "%~dp0"

REM --- 1. venv (created once) ------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [setup] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Python not found. Install Python 3.11+ and add it to PATH.
        pause
        exit /b 1
    )
    echo [setup] Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

REM --- 2. allow your Netlify site to call this API --------------------------
REM Replace with your real Netlify URL. Comma-separate multiple origins.
REM "*" also works and is fine here (public, stateless, no cookies).
if "%CORS_ORIGINS%"=="" set CORS_ORIGINS=*

REM --- 3. OPTIONAL: Gemma QC on your GPU server ----------------------------
REM Uncomment and point at your OpenAI-compatible endpoint to enable photo QC.
REM set GEMMA_ENDPOINT=http://YOUR-GPU-HOST:11434/v1/chat/completions
REM set GEMMA_API_KEY=

REM --- 4. run ----------------------------------------------------------------
REM 0.0.0.0 so the Cloudflare tunnel (and LAN) can reach it.
echo.
echo [run] Backend starting on http://0.0.0.0:8000
echo [run] CORS_ORIGINS=%CORS_ORIGINS%
echo.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000

pause
