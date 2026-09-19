@echo off
setlocal

title SealGuard - Local Demo Server
cd /d "%~dp0"

echo.
echo ========================================
echo        SealGuard Local Demo Server
echo ========================================
echo.

set "PYTHON_EXE=%~dp0venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    where python >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Python was not found.
        echo Install Python 3.11 or newer, then install the project requirements.
        echo.
        pause
        exit /b 1
    )
    set "PYTHON_EXE=python"
)

echo Using Python:
"%PYTHON_EXE%" --version
if errorlevel 1 (
    echo ERROR: Python could not be started.
    pause
    exit /b 1
)

rem Keep the normal SealGuard integrity scheduler enabled for the demo.
set "DEBUG=True"

echo.
echo Checking Django configuration...
"%PYTHON_EXE%" manage.py check
if errorlevel 1 (
    echo.
    echo ERROR: Django configuration check failed.
    pause
    exit /b 1
)

echo.
echo Applying database migrations...
"%PYTHON_EXE%" manage.py migrate --noinput
if errorlevel 1 (
    echo.
    echo ERROR: Database migration failed.
    pause
    exit /b 1
)

echo.
echo SealGuard is starting at:
echo http://127.0.0.1:8000
echo.
echo Keep this window open while presenting.
echo Press Ctrl+C, then Y, to stop the server.
echo.

"%PYTHON_EXE%" manage.py runserver 127.0.0.1:8000 --noreload

echo.
echo SealGuard has stopped.
pause
