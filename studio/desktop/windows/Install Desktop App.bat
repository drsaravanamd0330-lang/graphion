@echo off
rem Double-click this file to set up Journal Typesetting Studio on this computer
rem and put its icon on the Desktop and in the Start menu. Safe to run again.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
