@echo off
rem Builds the Windows installer:  dist\AI-Bridge-Setup-<version>.exe
rem Needs: Python 3.10+ and Inno Setup 6 (free: https://jrsoftware.org/isdl.php). Run from anywhere.
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  if defined BUILD_PY ( "%BUILD_PY%" -m venv .venv ) else ( py -3 -m venv .venv )
  if errorlevel 1 ( echo Couldn't create the Python environment. & exit /b 1 )
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip >nul
pip install -r requirements.txt pyinstaller
if errorlevel 1 ( echo Package install failed. & exit /b 1 )

echo.
echo [1/3] Building the app folder...
for /f %%v in ('python packaging\make_version_info.py') do set VER=%%v
pyinstaller --noconfirm --clean --distpath dist --workpath build packaging\aibridge.spec
if errorlevel 1 ( echo PyInstaller failed. & exit /b 1 )

echo.
echo [2/3] Checking the built app can load everything...
set "BRIDGE_HOME=%CD%\build\selfcheck"
if exist "%BRIDGE_HOME%\selfcheck.txt" del "%BRIDGE_HOME%\selfcheck.txt"
start /wait "" "dist\AI Bridge\AIBridge.exe" --selfcheck
set "BRIDGE_HOME="
if not exist "build\selfcheck\selfcheck.txt" ( echo The built app didn't start. See build\selfcheck\logs\app.log & exit /b 1 )
type "build\selfcheck\selfcheck.txt"
findstr /c:"MISSING" "build\selfcheck\selfcheck.txt" >nul && ( echo Something is missing from the build ^(see above^). & exit /b 1 )

echo.
echo [3/3] Making the installer...
set "ISCC="
for %%p in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe") do if exist %%p set "ISCC=%%~p"
if not defined ISCC ( echo Inno Setup 6 isn't installed. Get it free from https://jrsoftware.org/isdl.php and run me again. & exit /b 1 )
"%ISCC%" /Qp /DAppVersion=%VER% packaging\installer.iss
if errorlevel 1 ( echo The installer build failed. & exit /b 1 )

echo.
echo Done: dist\AI-Bridge-Setup-%VER%.exe
