@echo off
title Starting StudyQuest
cd /d "%~dp0"

if not exist run.bat (
    echo run.bat was not found in %~dp0.
    pause
    exit /b 1
)

if not exist run_worker.bat (
    echo run_worker.bat was not found in %~dp0.
    pause
    exit /b 1
)

if not exist venv (
    echo Virtual environment not found. Please run setup.bat first.
    pause
    exit /b 1
)

REM --- Conservative check for an already-running server on port 8000 ---
netstat -ano 2>nul | findstr /R /C:":8000 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [WARNING] Port 8000 appears to be in use already.
    echo If a StudyQuest server is already running, you may see an address in use error.
    echo.
)

echo Starting StudyQuest in two terminal windows...
echo.
echo Terminal 1: Web Application Server (http://127.0.0.1:8000/)
echo Terminal 2: Course Generation Background Worker
echo.

start "StudyQuest Web Server" cmd /k call "%~dp0run.bat"
start "StudyQuest Course Worker" cmd /k call "%~dp0run_worker.bat"

echo Both terminals have been launched.
echo.
echo Web application URL:
echo http://127.0.0.1:8000/
echo.
echo Keep both terminal windows open while using StudyQuest.
echo Closing the web server window stops the site.
echo Closing the worker window pauses background course generation.
echo.
timeout /t 4 /nobreak >nul
