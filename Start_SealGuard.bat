@echo off
setlocal

title SealGuard - Local Network Demo Server
cd /d "%~dp0"

echo.
echo ========================================
echo     SealGuard Local Network Demo Server
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

echo Starting the local-network launcher...
echo The launcher will display the phone URL automatically.
echo.

powershell -ExecutionPolicy Bypass -File "%~dp0scripts\start_local_demo.ps1"

echo.
echo SealGuard has stopped.
pause
