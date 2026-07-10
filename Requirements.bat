@echo off
title Save Requirements to File
color 0B

echo ==================================================
echo    Saving Installed Libraries
echo ==================================================

:: 1. Check if venv exists
if not exist venv (
    color 0C
    echo [ERROR] Virtual environment 'venv' folder not found.
    echo Please run 'run_production.bat' first to create it.
    pause
    exit /b
)

:: 2. Activate venv
call venv\Scripts\activate

:: 3. Save libraries to text file
echo [PROCESS] Reading all installed libraries...
pip freeze > requirements.txt

echo.
echo [SUCCESS] Your 'requirements.txt' has been updated!
echo You can now check the file to see the new libraries.
echo.
pause