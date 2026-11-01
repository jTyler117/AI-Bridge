@echo off
rem Builds a single AIBridge.exe you can put anywhere (dist\AIBridge.exe).
rem The .env file (your API keys) and the workspace\ folder are created NEXT TO the .exe.
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 ( echo Couldn't create the environment. & pause & exit /b 1 )
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip >nul
pip install -r requirements.txt pyinstaller
if errorlevel 1 ( echo Install failed. & pause & exit /b 1 )

pyinstaller --noconfirm --onefile --name AIBridge ^
  --add-data "ui;ui" ^
  --collect-all anthropic --collect-all openai --collect-all mcp --collect-all discord ^
  --hidden-import bridge_server --hidden-import cli_agents --hidden-import providers --hidden-import discord_bot ^
  --hidden-import playbooks --hidden-import report --hidden-import commands --hidden-import skills --hidden-import chats ^
  --hidden-import notify --hidden-import appearance --hidden-import connectors --hidden-import images --hidden-import memory ^
  --paths aibridge ^
  aibridge\app.py
if errorlevel 1 ( echo Build failed. & pause & exit /b 1 )

echo.
echo Checking the packaged app can load the AI libraries...
dist\AIBridge.exe --selfcheck
echo.
echo Done: dist\AIBridge.exe   (try: dist\AIBridge.exe --demo)
pause
