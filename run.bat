@echo off
title StudyQuest Web Server
cd /d "%~dp0"

if not exist venv (
    echo Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

echo ============================================================
echo  StudyQuest Web Server
echo ============================================================
echo.
echo  Web server : http://127.0.0.1:8000/
echo.
echo  Course generation runs in a separate worker process.
echo  Use start_studyquest.bat to launch both automatically,
echo  or run run_worker.bat in a second terminal.
echo.
echo  Press Ctrl+C here to stop the web server.
echo ============================================================
echo.

python manage.py runserver
pause
