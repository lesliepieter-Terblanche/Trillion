@echo off
title Trillion
rem --- Run as administrator. If we're already elevated, carry on.
net session >nul 2>&1
if %errorlevel% equ 0 goto elevated
rem Not elevated. Prefer the registered "Trillion" startup task, which starts her elevated with NO prompt.
schtasks /Query /TN "Trillion" >nul 2>&1
if %errorlevel% equ 0 (
  echo Starting Trillion as administrator ^(no prompt^)...
  schtasks /Run /TN "Trillion" >nul 2>&1
  exit /b
)
rem No task set up yet: fall back to the normal Windows admin prompt (click Yes). Run "5 - Start with Windows.bat"
rem once to set up the no-prompt elevated start.
echo Asking Windows for administrator rights...
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b
:elevated
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Trillion is not set up yet. Double-click "1 - Setup.bat" first.
  pause
  exit /b 1
)
echo Running as administrator.
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
