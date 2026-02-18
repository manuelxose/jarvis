@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_jarvis.ps1" %*
exit /b %errorlevel%
