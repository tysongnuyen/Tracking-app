@echo off
setlocal
cd /d "%~dp0"
if not exist "work\build-env\Scripts\python.exe" (
  py -3.12 -m venv "work\build-env"
  if errorlevel 1 exit /b 1
)
set "TRACKING_PYTHON=%~dp0work\build-env\Scripts\python.exe"
"%TRACKING_PYTHON%" -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1
"%TRACKING_PYTHON%" -c "import sys;sys.path.insert(0,'src');from browser_reader import ui_automation;ui_automation()"
if errorlevel 1 exit /b 1
"%TRACKING_PYTHON%" -m unittest discover -s tests -v
if errorlevel 1 exit /b 1
"%TRACKING_PYTHON%" -m PyInstaller --noconfirm --clean --onefile --windowed --name TrackingApp-Python --icon "%~dp0src\assets\tracking.ico" --add-data "%~dp0src\assets;assets" --distpath "." --workpath "work\frozen" --specpath "work" --paths "src" --hidden-import comtypes.gen.UIAutomationClient --hidden-import pystray._win32 --exclude-module pywinauto --exclude-module win32ui "src\app.py"
if errorlevel 1 exit /b 1
echo Build completed: TrackingApp-Python.exe
