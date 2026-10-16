@echo off
cd /d "%~dp0"
title Discord Bot (auto-restart)
rem يشغّل البوت ويعيد تشغيله تلقائيًا بعد أي انهيار. القفل داخل البوت يمنع تشغيل نسختين معًا.
rem للإيقاف: أغلق هذه النافذة. (الجهاز يجب أن يبقى مشغّلًا وغير نائم ليبقى البوت متصلًا)
set PY=
where py >nul 2>nul && set PY=py
if "%PY%"=="" ( python --version >nul 2>nul && set PY=python )
if "%PY%"=="" ( echo [X] Python is not installed. & pause & exit /b )
:loop
echo [%date% %time%] starting bot...
%PY% main.py
echo [%date% %time%] bot stopped (exit code %errorlevel%). Restarting in 10 seconds... (close this window to stop)
timeout /t 10 /nobreak >nul
goto loop
