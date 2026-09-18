@echo off
setlocal
pushd "%~dp0" || exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File ".\run_jarvis.ps1" %*
set "exit_code=%errorlevel%"
popd
exit /b %exit_code%
