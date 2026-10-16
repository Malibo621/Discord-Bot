@echo off
cd /d "%~dp0"
title Discord Bot
echo.
echo ===============================
echo    Discord Bot - Starting...
echo ===============================
echo.

set PY=
where py >nul 2>nul && set PY=py
if "%PY%"=="" ( python --version >nul 2>nul && set PY=python )
if "%PY%"=="" (
  echo [X] Python is not installed.
  echo     The download page will open now. Install it and CHECK "Add python.exe to PATH".
  echo     Then double-click start.bat again.
  start https://www.python.org/downloads/
  pause
  exit /b
)

where ffmpeg >nul 2>nul || (
  echo Installing FFmpeg for music, please wait...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements >nul 2>nul
)

if not exist ".installed-v2" (
  echo Installing required libraries, please wait...
  %PY% -m pip install -q -r requirements.txt || ( echo [X] Install failed. Check your internet. & pause & exit /b )
  echo ok> .installed-v2
)

if not exist ".env" (
  echo.
  echo Paste your bot token here ^(right-click to paste^) then press Enter:
  set /p TOKEN=Token: 
  setlocal enabledelayedexpansion
  echo Optional: paste your server ID for instant commands, or just press Enter:
  set /p GID=Server ID: 
  > .env echo DISCORD_TOKEN=!TOKEN!
  >> .env echo GUILD_ID=!GID!
  >> .env echo DATABASE_PATH=data/bot.db
  endlocal
)

echo.
echo Bot is running. Keep this window open. Close it to stop the bot.
echo.
%PY% main.py
echo.
pause
