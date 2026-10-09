@echo off
title Connect Trillion to GitHub
cd /d "%~dp0"
echo This links this folder to Trillion's GitHub repository, so she can update herself.
echo Your logins, keys and memory (the "data" folder) are not touched.
echo If a GitHub sign-in window opens, sign in. Close Trillion's black window first.
echo.
pause
git --version >nul 2>&1 || (echo Git is missing. Run "1 - Setup.bat" first. & pause & exit /b 1)
if not exist ".git" git init -q -b main
git remote remove origin >nul 2>&1
git remote add origin https://github.com/lesliepieter-Terblanche/Trillion.git
git fetch origin main
if errorlevel 1 (echo Could not reach the repository. Check the sign-in and try again. & pause & exit /b 1)
git reset --hard origin/main
git branch --set-upstream-to=origin/main main >nul 2>&1
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
echo.
echo Connected. Start Trillion again. From now on just tell her: "update yourself".
pause
