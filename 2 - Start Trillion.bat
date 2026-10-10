@echo off
title Trillion
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Trillion is not set up yet. Double-click "1 - Setup.bat" first.
  pause
  exit /b 1
)
echo Starting Trillion. Keep this window open while you use it.
echo On this PC:    http://localhost:8787
echo On your phone: http://YOUR-PC-TAILSCALE-ADDRESS:8787
start "" /b cmd /c "timeout /t 3 >nul & start http://localhost:8787"
:run
".venv\Scripts\python.exe" -m uvicorn app:app --host 0.0.0.0 --port 8787
if %errorlevel%==42 (
  echo Trillion updated herself. Restarting...
  goto run
)
if not defined TRILLION_SAFE (
  set TRILLION_SAFE=1
  echo.
  echo Trillion stopped unexpectedly. Starting her again in safe mode...
  goto run
)
echo.
echo Trillion stopped again. Please send Claude the last lines shown above.
pause
