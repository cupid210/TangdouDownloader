@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
python -m venv .build-venv
if errorlevel 1 goto failed
".build-venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-build.txt
if errorlevel 1 goto failed
".build-venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --console --name TangdouDownloader --icon assets\icon\download.ico main.py
if errorlevel 1 goto failed
copy /y "dist\TangdouDownloader.exe" "TangdouDownloader.exe" >nul
if errorlevel 1 goto failed
echo.
echo EXE ready: %~dp0TangdouDownloader.exe
pause
exit /b 0
:failed
echo Build failed. Please check the error above.
pause
exit /b 1
