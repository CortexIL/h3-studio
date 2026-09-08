@echo off
setlocal
title H3 Studio
cd /d "%~dp0"

rem First run: build the virtual environment and install dependencies.
rem Kept here so the user never has to open a terminal to set the project up.
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   First run - setting up. This takes a minute or two, once.
    echo.
    where python >nul 2>&1
    if errorlevel 1 (
        echo   Python is not installed.
        echo   Get it from https://www.python.org/downloads/  ^(tick "Add to PATH"^)
        echo.
        pause
        exit /b 1
    )
    python -m venv .venv
    if errorlevel 1 goto setupfail
    .venv\Scripts\python.exe -m pip install --upgrade pip --quiet
    .venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
    if errorlevel 1 goto setupfail
    echo   Setup done.
    echo.
)

if not exist "config.yaml" copy /y "config.example.yaml" "config.yaml" >nul

.venv\Scripts\python.exe -m app.launch %*
exit /b 0

:setupfail
echo.
echo   Setup failed. Check your internet connection and try again.
echo.
pause
exit /b 1
