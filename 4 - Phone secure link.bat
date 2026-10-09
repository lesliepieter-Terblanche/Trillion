@echo off
title Trillion - secure link for your phone
set TS=tailscale
where tailscale >nul 2>&1 || set TS="C:\Program Files\Tailscale\tailscale.exe"
%TS% version >nul 2>&1 || (echo Tailscale is not installed on this PC. Install it from tailscale.com first. & pause & exit /b 1)
echo Creating a secure (https) address for Trillion inside your private Tailscale network.
echo This is what lets the microphone work on your phone. Nothing is opened to the internet.
echo.
%TS% serve --bg 8787
echo.
echo If the lines above show a link asking you to enable HTTPS, open it, switch HTTPS on,
echo and then run this file again.
echo.
echo Your secure address (open this on the phone instead of the old one):
%TS% serve status
echo.
pause
