@echo off
title Install FreeVideo for Trillion
cd /d "%~dp0"
echo.
echo  FreeVideo makes AI video clips on this PC's own graphics card.
echo  It is a separate free program (github.com/FlashML-org/FreeVideo). Trillion only talks to it.
echo.
echo  Before you start:
echo   - It downloads large model files (tens of GB). Use a drive with plenty of free space.
echo   - Its installer shows the licence of the video model. Read it before you accept,
echo     especially if you plan to use the clips for a business.
echo.
set "FVDIR=%~dp0FreeVideo"
if not exist "%FVDIR%" mkdir "%FVDIR%"
if exist "%FVDIR%\FreeVideo.exe" goto run
echo  Downloading the FreeVideo installer...
curl.exe -L --fail --retry 3 -o "%FVDIR%\FreeVideo.exe.part" "https://github.com/FlashML-org/FreeVideo/releases/latest/download/FreeVideo.exe"
if errorlevel 1 (
  del "%FVDIR%\FreeVideo.exe.part" >nul 2>&1
  echo.
  echo  The download failed. Check the internet connection and run this file again,
  echo  or download FreeVideo.exe yourself from github.com/FlashML-org/FreeVideo and put it in:
  echo  %FVDIR%
  pause
  exit /b 1
)
ren "%FVDIR%\FreeVideo.exe.part" FreeVideo.exe
:run
echo.
echo  Opening FreeVideo. In its window:
echo   1. Choose "install a new ComfyUI" (or pick one you already have).
echo   2. Click "Install and launch" and wait. The first time takes a long while.
echo   3. When its page opens in the browser, FreeVideo is ready.
echo.
echo  After that, tell Trillion:  make an AI clip of a sunrise over the Magaliesberg
echo  FreeVideo must be open whenever you ask for an AI clip. Open it again later with this same file.
echo.
start "" "%FVDIR%\FreeVideo.exe"
pause
