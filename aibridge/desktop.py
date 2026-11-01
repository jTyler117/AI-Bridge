# AI Bridge: two AIs, one workspace.
# Copyright (C) 2026 Tyler Watson
# SPDX-License-Identifier: GPL-3.0-or-later  (see LICENSE)
"""The desktop shell: AI Bridge in its own window, with a tray icon, instead of a browser tab.

    python aibridge/app.py --window      (the installed app always starts this way)

- The window is the same page, shown in WebView2 (Edge's engine, built into Windows 10/11) through pywebview.
- Closing the window asks once: keep running in the tray (sessions carry on, Discord keeps working) or quit.
  Your answer can be remembered (Settings > Appearance > When I close the window).
- The tray icon: open the window, pause everything, emergency stop, quit.
- Only one copy runs. Starting it again just brings the open window to the front.
- Optional: start with Windows (minimised to the tray).

Everything here is a thin layer over the App object: no AI or safety logic lives in this file.
"""
import json
import os
import sys
import threading
import urllib.request
from pathlib import Path

import core

INSTANCE_FILE = "instance.json"  # in the data folder (the user's own profile): where the running copy listens
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "AI Bridge"


# --------------------------------------------------------------------------- one copy at a time
def _instance_path():
    return core.ROOT / INSTANCE_FILE


def show_running_copy():
    """If AI Bridge is already running, ask it to show its window. True = it did, so this launch can just exit."""
    info = core._read_json(_instance_path(), None)
    if not info or not info.get("port") or not info.get("token"):
        return False
    req = urllib.request.Request(f"http://127.0.0.1:{int(info['port'])}/api/desktop", data=json.dumps({"action": "show"}).encode(),
                                 headers={"Content-Type": "application/json", "X-Bridge-Token": info["token"]}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read() or b"{}").get("ok") is True
    except Exception:
        return False  # stale file (the app was killed): start normally


def _write_instance(app):
    core._write_json(_instance_path(), {"port": app.port, "token": app.token, "pid": os.getpid()})


def _clear_instance(app):
    info = core._read_json(_instance_path(), None) or {}
    if info.get("pid") == os.getpid():
        try:
            _instance_path().unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- start with Windows
def autostart_supported():
    return sys.platform == "win32" and getattr(sys, "frozen", False)


def autostart_enabled():
    if not autostart_supported():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on):
    """Only for the installed app (a source checkout has no stable .exe to point at)."""
    if not autostart_supported():
        raise ValueError("starting with Windows is only available in the installed app")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, f'"{sys.executable}" --window --background')
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass
    return autostart_enabled()


# --------------------------------------------------------------------------- the icon
def icon_path():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    for p in (base / "ui" / "icon.ico", base / "packaging" / "icon.ico"):
        if p.exists():
            return p
    return None


def _tray_image():
    from PIL import Image
    p = icon_path()
    if p:
        try:
            return Image.open(p)
        except Exception:
            pass
    return Image.new("RGBA", (64, 64), (91, 108, 255, 255))


# --------------------------------------------------------------------------- the shell
class Desktop:
    """Owns the window and the tray icon. The page talks to it through POST /api/desktop."""

    def __init__(self, app, background=False):
        self.app, self.background = app, background
        self.window = None
        self.tray = None
        self.quitting = False
        self.hidden = False
        app.desktop = self

    # ---- called by the page (POST /api/desktop) ----
    def handle(self, b):
        act = b.get("action")
        if act == "show":
            self.show()
            return {"ok": True}
        if act == "close":  # the answer to "keep running in the tray or quit?"
            choice = b.get("choice")
            if choice not in ("tray", "quit"):
                raise ValueError("choice must be tray or quit")
            if b.get("remember"):
                self.app.save_settings({"close_action": choice})
            threading.Thread(target=self.hide if choice == "tray" else self.quit, daemon=True).start()
            return {"ok": True}
        if act == "autostart":
            return {"ok": True, "autostart": set_autostart(bool(b.get("on")))}
        raise ValueError("unknown desktop action")

    def info(self):
        return {"window": True, "autostart_supported": autostart_supported(), "autostart": autostart_enabled()}

    # ---- window ----
    def show(self):
        if self.window is None:
            return
        self.hidden = False
        try:
            self.window.show()
            self.window.restore()
        except Exception:
            pass

    def hide(self):
        if self.window is None:
            return
        self.hidden = True
        try:
            self.window.hide()
        except Exception:
            pass
        self._notify("AI Bridge is still running", "It's in the tray. Sessions and Discord keep going.")

    def _on_closing(self):
        """Return False to keep the window open. Decides tray vs quit, or asks the page to ask you."""
        if self.quitting:
            return True
        action = self.app.get_settings()["close_action"]
        if action == "quit":
            threading.Thread(target=self.quit, daemon=True).start()
            return False
        if action == "tray":
            threading.Thread(target=self.hide, daemon=True).start()
            return False
        try:  # "ask": the page shows the choice (an in-app dialog in the current theme, not a Windows box)
            self.window.evaluate_js("window.askClose && window.askClose()")
        except Exception:
            threading.Thread(target=self.hide, daemon=True).start()
        return False

    # ---- tray ----
    def _notify(self, title, text):
        try:
            if self.tray is not None and getattr(self.tray, "HAS_NOTIFICATION", False):
                self.tray.notify(text, title)
        except Exception:
            pass

    def _status_text(self):
        s = self.app.session
        try:
            st = s.status() if callable(getattr(s, "status", None)) else ""
        except Exception:
            st = ""
        return f"Session: {st or 'idle'}"

    def _start_tray(self):
        try:
            import pystray
        except ImportError:
            print("pystray isn't installed: no tray icon (pip install pystray). Closing the window will quit.")
            return
        M = pystray.MenuItem

        def pause_all(icon=None, item=None):
            try:
                self.app.session.pause()
            except Exception:
                pass

        def emergency(icon=None, item=None):
            try:
                self.app.session.emergency_stop()
            except Exception:
                pass

        menu = pystray.Menu(M("Open AI Bridge", lambda i, it: self.show(), default=True),
                            M(lambda it: self._status_text(), None, enabled=False),
                            pystray.Menu.SEPARATOR,
                            M("Pause", pause_all),
                            M("Emergency stop", emergency),
                            pystray.Menu.SEPARATOR,
                            M("Quit AI Bridge", lambda i, it: threading.Thread(target=self.quit, daemon=True).start()))
        self.tray = pystray.Icon("ai-bridge", _tray_image(), "AI Bridge", menu)
        self.tray.run_detached()

    # ---- life cycle ----
    def quit(self):
        if self.quitting:
            return
        self.quitting = True
        app = self.app
        try:
            app.session.stop()
        except Exception:
            pass
        try:
            app.discord.stop(wait=False)
        except Exception:
            pass
        _clear_instance(app)
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        try:
            if self.window is not None:
                self.window.destroy()
        except Exception:
            pass
        try:
            app.httpd.shutdown()
        except Exception:
            pass

    def run(self):
        """Blocks until you quit. Raises ImportError when pywebview isn't installed (the caller falls back to the browser)."""
        import webview
        app = self.app
        app.serve_in_thread()
        _write_instance(app)
        self.window = webview.create_window("AI Bridge", app.url, width=1320, height=860, min_size=(900, 600),
                                            hidden=self.background, background_color="#0f1117")
        self.hidden = self.background
        self.window.events.closing += self._on_closing
        self._start_tray()
        try:
            webview.start(private_mode=False, storage_path=str(core.ROOT / "webview"), icon=str(icon_path() or "") or None)
        finally:
            self.quit()
