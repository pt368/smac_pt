@echo off
title Smart Object Auto Clicker
cd /d "%~dp0"

set "SYS_PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"
if not exist "%SYS_PYTHON%" set "SYS_PYTHON=python"

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python virtual environment...
    "%SYS_PYTHON%" -m venv .venv
    call .venv\Scripts\activate.bat
    echo Installing required dependencies...
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

".venv\Scripts\python.exe" app.py
pause
