@echo off
rem Removes the Desktop and Start menu shortcuts. Your articles and journal
rem profiles are not touched.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Uninstall
echo.
pause
