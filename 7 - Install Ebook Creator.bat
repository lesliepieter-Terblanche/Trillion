@echo off
title Install Ebook Creator for Trillion
cd /d "%~dp0"
echo.
echo  Ebook Creator lays out the ebooks Trillion writes: cover, contents page, chapter pages.
echo  It is a separate free program (github.com/victornns/ebook-creator, MIT licence).
echo.
where node >nul 2>&1
if errorlevel 1 (
  echo  Step 1 of 3: installing Node.js, which Ebook Creator runs on...
  winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
  echo.
  echo  Node.js is installed. Close this window and double-click this file ONCE MORE to finish.
  pause
  exit /b 0
)
if not exist "EbookCreator\package.json" (
  echo  Step 2 of 3: downloading Ebook Creator...
  git clone --depth 1 https://github.com/victornns/ebook-creator.git EbookCreator
  if errorlevel 1 (
    echo.
    echo  The download failed. Check the internet connection and run this file again.
    pause
    exit /b 1
  )
)
echo  Step 3 of 3: installing its parts. This takes a few minutes...
cd EbookCreator
call npm install --no-audit --no-fund
if errorlevel 1 (
  echo.
  echo  That did not finish. Run this file again; if it fails twice, send Claude the lines above.
  pause
  exit /b 1
)
cd ..
echo.
echo  Done. Tell Trillion:  write an ebook about getting your home ready to sell
echo  or use the Files page, Make an ebook.
echo.
pause
