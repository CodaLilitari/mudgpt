@echo off
rem Take-Over-Krasna.cmd - take control of Krasna's game session from Red.
rem Red's commands are refused until you run Release-To-Red.cmd.
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%~dp0..\..\.venv\Scripts\python.exe"
set "PYTHONPATH=%~dp0src"
"%PY%" -m mudgpt --control "%~dp0control.json" claim human --takeover
echo.
echo You hold control now. Type game commands yourself with:
echo   "%PY%" -m mudgpt --control control.json send "look" --view plain
echo (run that from this folder with PYTHONPATH=src). Release-To-Red.cmd hands control back.
pause
