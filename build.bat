@echo off
REM Builds MediaFireModManager.exe into the "dist" folder.
REM Needs Python 3.9+ from python.org (tick "Add python.exe to PATH" when installing).
cd /d "%~dp0"

echo Installing requirements...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

python set_version.py
if errorlevel 1 goto :fail

echo Building exe...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name MediaFireModManager ^
  --collect-all tkinterdnd2 ^
  --collect-all customtkinter ^
  --hidden-import darkdetect ^
  --hidden-import keyring.backends.Windows ^
  mediafire_mod_manager.py
if errorlevel 1 goto :fail

echo.
echo Done!  Your app is at:  %~dp0dist\MediaFireModManager.exe
echo.
echo This is a local test build. To release a version that every copy of the app
echo picks up, push a version tag to GitHub (see README.md).
pause
exit /b 0

:fail
echo.
echo Build failed - scroll up to see the error.
pause
exit /b 1
