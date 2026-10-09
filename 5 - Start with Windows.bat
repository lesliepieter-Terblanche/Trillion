@echo off
title Trillion - start with Windows
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Startup'),'Trillion.lnk')); $s.TargetPath='%~dp02 - Start Trillion.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
echo Trillion will now start by itself each time you sign in to Windows.
echo To undo this, delete "Trillion" from the Startup folder (press Win+R, type shell:startup).
echo.
echo For remote access the PC must stay awake. Turning off sleep while plugged in:
powercfg /change standby-timeout-ac 0
echo Sleep while plugged in is now off. The screen can still turn off.
pause
