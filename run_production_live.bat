@echo off
title Cafe ERP - Live (ASGI/Websockets) Launcher
color 0A

:: ------------------------------------------------
:: Same as run_production.bat, but serves via Daphne (ASGI) so the
:: KDS / waiter table-map / delivery screens get live websocket updates.
:: ------------------------------------------------
set VENV_NAME=venv
set PORT=8085

echo ==================================================
echo    Cafe ERP - Live Production Launcher
echo ==================================================

python --version >nul 2>&1
if %errorlevel% neq 0 (
    color 0C
    echo [ERROR] Python is not installed or not in PATH.
    pause
    exit /b
)

if not exist %VENV_NAME% (
    echo [SETUP] Creating virtual environment '%VENV_NAME%'...
    python -m venv %VENV_NAME%
)

call %VENV_NAME%\Scripts\activate

echo [SETUP] Checking dependencies...
pip install -r requirements.txt >nul 2>&1

echo [SYSTEM] Applying database migrations...
python manage.py migrate --noinput >nul

echo [SYSTEM] Collecting static files (CSS/JS)...
set DJANGO_SETTINGS_MODULE=textile_pos.production_settings
python manage.py collectstatic --noinput >nul

cls
echo ==================================================
echo    Cafe ERP is running (live updates enabled)!
echo ==================================================
echo.
echo    Open your browser to: http://localhost:%PORT%
echo.
echo    (To stop the system, close this window)
echo.

python daphne_server.py

pause
