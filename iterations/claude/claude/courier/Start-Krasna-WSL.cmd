@echo off
rem ---------------------------------------------------------------------------
rem  Start-Krasna-WSL.cmd - run Krasna's mailbox courier inside WSL (Linux).
rem  Opens your default WSL distribution in this folder and runs
rem  start-krasna-wsl.sh, which prepares ~/.krasna on first run and then starts
rem  the courier. Leave the window open; Ctrl+C stops it (quit in-game first).
rem ---------------------------------------------------------------------------
title Krasna - MudGPT courier (WSL)
wsl.exe --cd "%~dp0." -e bash ./start-krasna-wsl.sh
echo.
echo Courier stopped.
pause
