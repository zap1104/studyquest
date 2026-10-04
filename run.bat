@echo off
title StudyQuest
cd /d "%~dp0"

if not exist venv (
    echo Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

echo ============================================================
echo  StudyQuest
echo ============================================================
echo.
echo  Web server : http://127.0.0.1:8000/
echo.
echo  Course generation runs in a SECOND process. This script
echo  starts it automatically in its own window, titled
echo  "StudyQuest Worker". Keep that window open.
echo.
echo  If a course sits on "Preparing" and never finishes, check
echo  the worker window - it will say why.
echo.
echo  Press Ctrl+C here to stop the web server.
echo  Close the worker window (or Ctrl+C in it) to stop generation.
echo ============================================================
echo.

REM --- Start the generation worker in a separate window -------------
start "StudyQuest Worker" /D "%~dp0" cmd /k "call venv\Scripts\activate.bat && python manage.py run_course_generation_worker"

REM --- Give the worker a moment before the server takes this terminal ---
timeout /t 2 /nobreak >nul

echo Worker started in a separate window.
echo.
python manage.py runserver
pause
