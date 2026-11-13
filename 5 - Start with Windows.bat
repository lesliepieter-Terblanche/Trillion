@echo off
title Trillion - start with Windows
rem --- Registering an elevated startup task needs administrator rights. Relaunch elevated if needed.
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Asking Windows for administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
cd /d "%~dp0"
rem --- Remove the old (non-elevated) Startup-folder shortcut if it exists, so Trillion doesn't start twice.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=[IO.Path]::Combine([Environment]::GetFolderPath('Startup'),'Trillion.lnk'); if(Test-Path $p){Remove-Item $p -Force}"
rem --- Register a logon task that starts Trillion AS ADMINISTRATOR each time you sign in (no UAC prompt at boot).
powershell -NoProfile -ExecutionPolicy Bypass -Command "$a=New-ScheduledTaskAction -Execute '%~dp02 - Start Trillion.bat'; $t=New-ScheduledTaskTrigger -AtLogOn; $p=New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest; $s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero); Register-ScheduledTask -TaskName 'Trillion' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null"
if %errorlevel% neq 0 (
  echo Could not register the startup task. Try running this file as administrator.
  pause
  exit /b 1
)
echo.
echo Done. Trillion will now start automatically - as administrator - each time you sign in to Windows.
echo To undo this later: open PowerShell and run   Unregister-ScheduledTask -TaskName Trillion -Confirm:$false
echo.
echo For remote access the PC must stay awake. Turning off sleep while plugged in:
powercfg /change standby-timeout-ac 0
echo Sleep while plugged in is now off. The screen can still turn off.
pause
