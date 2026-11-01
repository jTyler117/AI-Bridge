# AI Bridge · Copyright (C) 2026 Tyler Watson · SPDX-License-Identifier: GPL-3.0-or-later
# PyInstaller recipe for the Windows app.   Build with:  packaging\build_windows.bat
# One folder (dist\AI Bridge\) instead of one big .exe: it starts faster and antivirus tools like it better.
# The installer (installer.iss) packs that folder up.
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "aibridge")

datas = [(os.path.join(ROOT, "ui"), "ui")]
binaries = []
hiddenimports = [
    # the app's own modules (imported by name at runtime, so PyInstaller can't always see them)
    "app", "appearance", "approve", "bridge_server", "chats", "cli_agents", "commands", "connectors", "core", "desktop",
    "discord_bot", "images", "memory", "notify", "onboarding", "playbooks", "providers", "report", "skills", "version",
    # the window + tray on Windows
    "webview.platforms.edgechromium", "webview.platforms.winforms", "pystray._win32", "clr_loader",
]
for pkg in ("anthropic", "openai", "mcp", "discord", "webview", "pystray", "pydantic", "httpx", "anyio", "uvicorn", "starlette"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass  # a package that isn't installed is reported by `AIBridge.exe --selfcheck`
hiddenimports += collect_submodules("dotenv")

a = Analysis(
    [os.path.join(SRC, "app.py")],
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "numpy", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AIBridge",
    icon=os.path.join(ROOT, "ui", "icon.ico"),
    console=False,          # a desktop app: no black console window (output goes to <data folder>\logs\app.log)
    version=os.path.join(SPECPATH, "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="AI Bridge")
