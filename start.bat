@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist ".deps" set "PYTHONPATH=%~dp0.deps;%PYTHONPATH%"
set "PYTHONIOENCODING=utf-8"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py %*
) else if exist "python-3.8.3-embed-amd64\python.exe" (
    "python-3.8.3-embed-amd64\python.exe" main.py %*
) else (
    python main.py %*
)
if errorlevel 1 pause
endlocal
