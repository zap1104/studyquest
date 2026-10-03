@echo off
title StudyQuest Background Worker
cd /d "%~dp0"

if not exist venv (
    echo Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
echo Starting the Course Forge generation worker.
echo This process generates courses in the background.
echo Keep it running alongside run.bat, or queued courses will not be built.
echo Press Ctrl+C to stop.
python manage.py run_course_generation_worker
pause