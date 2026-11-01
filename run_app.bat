@echo off
rem Double-click me. First run sets everything up (about a minute), after that it just opens the app.
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required. Get it from https://www.python.org/downloads/
  echo During install, tick "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo First run: creating a private Python environment and installing packages...
  py -3 -m venv .venv
  if errorlevel 1 ( echo Couldn't create the environment. & pause & exit /b 1 )
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip >nul
  pip install -r requirements.txt
  if errorlevel 1 ( echo Package install failed. Check your internet and Python version ^(3.10+^). & pause & exit /b 1 )
  copy /y requirements.txt ".venv\requirements.installed" >nul
) else (
  call ".venv\Scripts\activate.bat"
)

rem An update can add packages (e.g. discord.py): install them when requirements.txt changed since the last install.
fc /b requirements.txt ".venv\requirements.installed" >nul 2>&1
if errorlevel 1 (
  echo Installing what this update needs...
  pip install -r requirements.txt
  if errorlevel 1 ( echo Package install failed. Check your internet connection. & pause & exit /b 1 )
  copy /y requirements.txt ".venv\requirements.installed" >nul
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo This needs Python 3.10 or newer. Delete the .venv folder, install a newer Python, and run me again.
  pause
  exit /b 1
)

echo Starting AI Bridge... your browser will open. Close this window to quit.
python aibridge\app.py %*
if errorlevel 1 pause
