# AI Bridge: two AIs, one workspace.
# Copyright (C) 2026 Tyler Watson
# SPDX-License-Identifier: GPL-3.0-or-later  (see LICENSE)
"""First-run setup: what the setup wizard needs from the app.

- status(): is setup done, which AI agents are installed / signed in, is there an older AI Bridge to bring over.
- install_agent(): runs Anthropic's or OpenAI's (or Ollama's) OFFICIAL installer, in a window you can watch.
- sign_in(): opens the agent's own sign-in (your browser does the rest). AI Bridge never sees your password.
- import_from(): COPIES chats, memory, playbooks, skills, settings and keys from an older folder. The old folder is never
  changed, and anything already here is backed up first.
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cli_agents
import core

# The official one-line installers (no admin needed). Downloaded at install time, straight from the vendor.
INSTALLERS = {
    "claude": {"name": "Claude Code", "by": "Anthropic", "ps": "irm https://claude.ai/install.ps1 | iex",
               "needs": "a paid Claude plan (Pro, Max, Team or Enterprise)"},
    "chatgpt": {"name": "Codex", "by": "OpenAI", "ps": "irm https://chatgpt.com/codex/install.ps1 | iex",
                "needs": "a paid ChatGPT plan (Plus, Pro, Business, Edu or Enterprise)"},
    "ollama": {"name": "Ollama", "by": "Ollama", "ps": "$f = Join-Path $env:TEMP 'OllamaSetup.exe'; irm https://ollama.com/download/OllamaSetup.exe -OutFile $f; Start-Process $f -Wait",
               "needs": "nothing: it's free and runs on your PC"},
}
SIGNIN_CMD = {"claude": ["auth", "login"], "chatgpt": ["login"]}

# What an older AI Bridge folder holds that's worth bringing over (relative to its root)
_KEEP_DIRS = ("workspace",)
_SKIP_IN_WORKSPACE = {"__pycache__"}
_ENV_NAME = re.compile(r"^\s*([A-Z][A-Z0-9_]{1,63})\s*=")


def _settings_path():
    return core.DATA / "setup.json"


def done():
    return bool((core._read_json(_settings_path(), {}) or {}).get("done"))


def mark_done(on=True):
    d = core._read_json(_settings_path(), {}) or {}
    d.update(done=bool(on), when=time.strftime("%Y-%m-%d %H:%M"))
    core._write_json(_settings_path(), d)


# --------------------------------------------------------------------------- agents
def agent_status(demo=False):
    out = {}
    for a in ("claude", "chatgpt"):
        info = dict(INSTALLERS[a], installed=False, signed_in=False, detail="")
        if demo:
            info.update(installed=True, signed_in=True, detail="demo mode")
        else:
            found, ok, detail = cli_agents.login_status(a)
            info.update(installed=found, signed_in=ok, detail=detail)
        out[a] = info
    out["ollama"] = dict(INSTALLERS["ollama"], installed=bool(demo or shutil.which("ollama") or shutil.which("ollama", path=cli_agents._fresh_path())))
    return out


def _console(args, title):
    """Run something in its own visible console window, so you can watch it (and type if it asks). Windows only."""
    if os.name != "nt":
        raise ValueError("this is only available on Windows; run it in a terminal yourself: " + " ".join(args))
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)
    env = cli_agents.clean_env({"PATH": cli_agents._fresh_path()})  # no API keys: a key would switch the CLI to paid billing
    # "&" (not "&&") so the window stays open to show an error too
    subprocess.Popen(["cmd", "/c", "title", title.replace(" ", "_"), "&"] + args + ["&", "echo.", "&", "echo", "Finished.", "You", "can", "close", "this",
                      "window", "and", "press", "Check", "again", "in", "AI", "Bridge.", "&", "pause"], creationflags=flags, env=env, close_fds=True)


def install_agent(which):
    if which not in INSTALLERS:
        raise ValueError("unknown agent")
    ps = INSTALLERS[which]["ps"]
    _console(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], f"Installing {INSTALLERS[which]['name']}")
    return {"ok": True, "note": f"Installing {INSTALLERS[which]['name']} in its own window. Press Check again when it says Done."}


def sign_in(which):
    if which not in SIGNIN_CMD:
        raise ValueError("unknown agent")
    base = cli_agents.cli_command(which)
    if not base:
        raise ValueError(f"{INSTALLERS[which]['name']} isn't installed yet. Install it first.")
    _console(base + SIGNIN_CMD[which], f"Sign in to {INSTALLERS[which]['name']}")
    return {"ok": True, "note": "Finish signing in in your browser, then press Check again."}


# --------------------------------------------------------------------------- an older AI Bridge
def _candidates():
    home = Path.home()
    c = [Path("C:/ai-bridge"), Path("C:/AI-Bridge"), home / "ai-bridge", home / "Desktop" / "ai-bridge", home / "Documents" / "ai-bridge",
         home / "Downloads" / "ai-bridge", home / "Downloads" / "ai-bridge-main"]
    extra = os.getenv("BRIDGE_IMPORT_FROM")
    if extra:
        c.insert(0, Path(extra))
    return c


def describe(root):
    """What's in an AI Bridge folder (counts only, never contents). None if it isn't one."""
    root = Path(root)
    ws = root / "workspace"
    if not ws.is_dir() or not ((ws / "chats.json").exists() or (ws / "state.json").exists() or (ws / "transcript.json").exists()):
        return None
    chats_ = core._read_json(ws / "chats.json", {}) or {}
    n_chats = len(chats_.get("chats") or {}) or (1 if (ws / "transcript.json").exists() else 0)
    pb = core._read_json(ws / "playbooks.json", {}) or {}
    skills_dir = ws / "skills"
    mem = ws / "memory"
    keys = []
    envf = root / ".env"
    if envf.exists():
        try:
            for ln in envf.read_text(encoding="utf-8", errors="replace").splitlines():
                m = _ENV_NAME.match(ln)
                if m and ln.split("=", 1)[1].strip():
                    keys.append(m.group(1))
        except OSError:
            pass
    return {"path": str(root), "chats": n_chats, "playbooks": len([p for p in pb.get("items", []) if not p.get("starter")]),
            "skills": len([p for p in skills_dir.iterdir() if p.is_dir()]) if skills_dir.is_dir() else 0,
            "memory": mem.is_dir() and any(mem.iterdir()), "keys": sorted(set(keys)),
            "connectors": (ws / "connectors.json").exists(), "discord": (ws / "discord.json").exists()}


def find_old():
    here = core.ROOT.resolve()
    for c in _candidates():
        try:
            if c.resolve() == here:
                continue
        except OSError:
            continue
        d = describe(c)
        if d:
            return d
    return None


def import_from(src):
    """Copy an older AI Bridge's data in. The old folder isn't touched. Returns what was copied."""
    src = Path(src).resolve()
    info = describe(src)
    if not info:
        raise ValueError("that folder doesn't look like an AI Bridge folder (no workspace with chats in it)")
    if src == core.ROOT.resolve():
        raise ValueError("that's this app's own folder")
    dst_ws = core.DATA
    backup = None
    if dst_ws.exists() and any(dst_ws.iterdir()):
        backup = core.ROOT / f"_before_import_{time.strftime('%Y%m%d_%H%M%S')}"
        shutil.copytree(dst_ws, backup / "workspace", dirs_exist_ok=True)
        envf = core.ROOT / ".env"
        if envf.exists():
            shutil.copy2(envf, backup / ".env")
    shutil.copytree(src / "workspace", dst_ws, dirs_exist_ok=True,
                    ignore=lambda d, names: [n for n in names if n in _SKIP_IN_WORKSPACE or n.endswith(".lock") or n == "instance.json"])
    added = _merge_env(src / ".env", core.ROOT / ".env")
    (dst_ws / "halt.flag").unlink(missing_ok=True)  # an old emergency stop shouldn't greet you in the new app
    return {"ok": True, "copied": info, "keys_added": added, "backup": str(backup) if backup else None}


def _merge_env(src, dst):
    """Bring over keys this app doesn't have yet. Existing values here win. Returns the names added (never the values)."""
    if not src.exists():
        return []
    have = {}
    lines = dst.read_text(encoding="utf-8").splitlines() if dst.exists() else []
    for ln in lines:
        m = _ENV_NAME.match(ln)
        if m:
            have[m.group(1)] = ln.split("=", 1)[1].strip()
    added = []
    for ln in src.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _ENV_NAME.match(ln)
        if not m:
            continue
        name, val = m.group(1), ln.split("=", 1)[1].strip()
        if val and not have.get(name):
            lines = [x for x in lines if not (_ENV_NAME.match(x) and _ENV_NAME.match(x).group(1) == name)]
            lines.append(f"{name}={val}")
            os.environ[name] = val.strip('"').strip("'")
            added.append(name)
    if added:
        tmp = dst.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, dst)
    return added


def is_frozen():
    return bool(getattr(sys, "frozen", False))
