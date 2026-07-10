@echo off
title Textile POS - Production Launcher
color 0A

:: ------------------------------------------------
:: Configuration
:: ------------------------------------------------
set VENV_NAME=venv
set PORT=8000

echo ==================================================
echo    Textile POS System - Production Launcher
echo ==================================================

:: 1. Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    color 0C
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python from https://www.python.org/
    pause
    exit /b
)

:: 2. Create Virtual Environment if missing
if not exist %VENV_NAME% (
    echo [SETUP] Creating virtual environment '%VENV_NAME%'...
    python -m venv %VENV_NAME%
)

:: 3. Activate Virtual Environment
call %VENV_NAME%\Scripts\activate

:: 4. Install Dependencies
echo [SETUP] Checking dependencies...
pip install -r requirements.txt >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Installing requirements for the first time...
    pip install -r requirements.txt
)

:: 5. Prepare Database & Static Files
echo [SYSTEM] Applying database migrations...
python manage.py migrate --noinput >nul

echo [SYSTEM] Collecting static files (CSS/JS)...
:: We use the production settings for this to ensure WhiteNoise works
set DJANGO_SETTINGS_MODULE=textile_pos.production_settings
python manage.py collectstatic --noinput >nul

:: 6. Start the Server
cls
echo ==================================================
echo    Textile POS is running!
echo ==================================================
echo.
echo    Open your browser to: http://localhost:%PORT%
echo.
echo    (To stop the system, close this window)
echo.

python waitress_server.py

pause