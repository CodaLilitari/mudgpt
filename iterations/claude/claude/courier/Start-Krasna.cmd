@echo off
rem ---------------------------------------------------------------------------
rem  Start-Krasna.cmd - Krasna's mailbox courier (Red's own MudGPT copy)
rem
rem  Starts a private MudGPT broker for Red's Legends of Krynn character and
rem  carries Red's requests from the mailbox folder, one command at a time.
rem  This window logs every command Red sends (the password shows as [password]).
rem
rem  Stop:      Ctrl+C or close the window. Quit in-game first if you can:
rem             closing the socket is not an in-game save.
rem  Take over: double-click Take-Over-Krasna.cmd (Release-To-Red.cmd hands back).
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
title Krasna - MudGPT courier
call :find_python || goto :no_python
"%PY%" "%~dp0mailbox_courier.py"
echo.
echo Courier stopped.
pause
exit /b 0

:find_python
set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" exit /b 0
where uv >nul 2>nul
if not errorlevel 1 (
  echo First run: creating Red's own Python environment with uv...
  uv venv --python 3.12 "%~dp0.venv"
  uv pip install --python "%~dp0.venv\Scripts\python.exe" telnetlib3==4.0.3
  if exist "%PY%" exit /b 0
)
rem Fallback: borrow the interpreter of the MudGPT folder this one sits in (read-only use).
set "PY=%~dp0..\..\.venv\Scripts\python.exe"
if exist "%PY%" exit /b 0
exit /b 1

:no_python
echo Could not find a Python environment with MudGPT's dependencies.
echo Install uv (https://docs.astral.sh/uv/) or create .venv in this folder.
pause
exit /b 1
