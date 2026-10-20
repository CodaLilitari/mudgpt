@echo off
rem Release-To-Red.cmd - hand Krasna's session back after a takeover.
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%~dp0..\..\.venv\Scripts\python.exe"
set "PYTHONPATH=%~dp0src"
"%PY%" -m mudgpt --control "%~dp0control.json" release human
echo.
echo Released. Red can claim control again.
pause
