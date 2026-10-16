@echo off
rem Double-click to install the Cura AI Assistant for the current user.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
