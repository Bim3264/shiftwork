@echo off
setlocal
REM ============================================================
REM  Run ShiftWork web locally with NO Google auth (dev only).
REM  Installs dependencies, then starts the server. The window
REM  stays open on any error so you can read the message.
REM ============================================================
cd /d "%~dp0.."

echo === ShiftWork (local dev, no Google sign-in) ===
echo.

echo [1/3] Checking Python...
python --version
if errorlevel 1 (
  echo.
  echo ERROR: 'python' was not found. Install Python 3.10+ from python.org
  echo        and make sure "Add python to PATH" is checked, then re-run.
  echo.
  pause
  exit /b 1
)

echo.
echo [2/3] Installing dependencies (first run may take a minute)...
python -m pip install -r webapp\requirements.txt
if errorlevel 1 (
  echo.
  echo ERROR: dependency install failed. See the messages above.
  echo.
  pause
  exit /b 1
)

set DEV_AUTH_BYPASS=1
set SESSION_SECRET=dev
REM Run solves in-process so no Redis/worker is needed locally. The page waits
REM while it solves; cap the search so it returns reasonably fast.
set SOLVE_INLINE=1
set SOLVER_MAX_TIME_SECONDS=30
echo.
echo [3/3] Starting server at http://127.0.0.1:8000   (press Ctrl+C to stop)
echo        You are the local "dev@localhost" user - no login needed.
echo.
python -m uvicorn webapp.web.app:app --port 8000

echo.
echo === server stopped ===
pause
