# AI Bridge: two AIs, one workspace.
# Copyright (C) 2026 Tyler Watson
# SPDX-License-Identifier: GPL-3.0-or-later  (see LICENSE)
"""Notifications: tell you when something happens, in Discord (through the AI Bridge bot) and on this PC.

The Discord side lives in discord_bot.py: it registers itself here as a "sink" while the bot is online and linked.
Quiet hours: on this PC nothing pops up; in Discord the message still arrives, just without pinging you.
Secrets never go out: everything is passed through core.redact() first.

Config: <data>/notify.json (app-owned; the AIs can't write it).
"""
import json
import re
import threading
import time

import core

EVENTS = {"finished": "Session finished", "waiting": "Waiting on you", "error": "Something broke",
          "limit": "Usage limit / connection (paused, resumed)", "stuck": "An AI looks stuck", "progress": "Progress check-ins"}
DEFAULTS = {"desktop": {"enabled": True},
            "events": {"finished": True, "waiting": True, "error": True, "limit": True, "stuck": True, "progress": False},
            "progress_minutes": 30,
            "quiet": {"enabled": False, "start": "23:00", "end": "07:00"}}
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_RECENT = {}  # (event, key) -> time last sent, so the same thing doesn't ping you twice in a minute
_LOCK = threading.Lock()
DESKTOP_QUEUE = []  # picked up by the app window (Windows / browser notification)
SENT_LOG = []  # last few sends, shown in Settings
SINKS = {}  # name -> fn(event, title, body, key, quiet): other places a notification goes (the Discord bot)


def _path():
    return core.DATA / "notify.json"


def get():
    s = json.loads(json.dumps(DEFAULTS))
    saved = core._read_json(_path(), {}) or {}
    for k, v in saved.items():
        if k in s and isinstance(s[k], dict) and isinstance(v, dict):
            s[k].update({kk: vv for kk, vv in v.items() if kk in s[k]})
        elif k in s:
            s[k] = v
    return s


def save(b):
    s = get()
    if "desktop" in b:
        s["desktop"]["enabled"] = bool((b["desktop"] or {}).get("enabled", s["desktop"]["enabled"]))
    if "events" in b:
        for k, v in (b["events"] or {}).items():
            if k in EVENTS:
                s["events"][k] = bool(v)
    if "progress_minutes" in b:
        s["progress_minutes"] = max(5, min(240, int(b["progress_minutes"])))
    if "quiet" in b:
        q = b["quiet"] or {}
        if "enabled" in q:
            s["quiet"]["enabled"] = bool(q["enabled"])
        for k in ("start", "end"):
            if k in q:
                if not _TIME.match(str(q[k])):
                    raise ValueError("quiet hours use 24-hour times like 23:00")
                s["quiet"][k] = str(q[k])
    core._write_json(_path(), {k: s[k] for k in DEFAULTS})  # old ntfy / webhook settings are dropped on the next save
    return get()


def _in_quiet(s, now=None):
    if not s["quiet"]["enabled"]:
        return False
    t = time.strftime("%H:%M", time.localtime(now or time.time()))
    a, b = s["quiet"]["start"], s["quiet"]["end"]
    return (a <= t < b) if a <= b else (t >= a or t < b)


def _chat_name():
    try:
        import chats
        c = chats.active()
        return c["name"] if c else ""
    except Exception:
        return ""


def send(event, text, preview="", key="", force=False, wait=False):
    """Send one notification everywhere it's switched on. Never raises; returns where it went.
    key: what makes two events 'the same' (e.g. an approval id) for the one-minute de-duplication.
    preview: kept for callers; the Discord thread already shows the conversation, so it isn't repeated."""
    try:
        s = get()
    except Exception:
        return []
    quiet = False
    if not force:
        if event not in EVENTS or not s["events"].get(event):
            return []
        with _LOCK:
            k = (event, key or text)
            if time.time() - _RECENT.get(k, 0) < 60:
                return []
            _RECENT[k] = time.time()
        quiet = _in_quiet(s)
    chat = _chat_name()
    title = (f"{chat}: " if chat else "AI Bridge: ") + EVENTS.get(event, "Test")
    body = core.redact(text)
    targets, results = [], {}
    if s["desktop"]["enabled"] and not quiet:
        with _LOCK:
            DESKTOP_QUEUE.append({"id": f"{time.time():.6f}", "title": title, "body": body, "event": event})
            del DESKTOP_QUEUE[:-20]
        targets.append("desktop")
    for name, fn in list(SINKS.items()):
        try:
            fn(event, title, body, key, quiet)
            results[name] = "sent" + (" (quiet hours: no ping)" if quiet else "")
            targets.append(name)
        except Exception as e:  # a notification must never take a session down
            results[name] = f"failed: {type(e).__name__}: {e}"[:200]
    with _LOCK:
        SENT_LOG.append({"ts": time.strftime("%H:%M:%S"), "event": event, "title": title, "to": targets, "results": results})
        del SENT_LOG[:-20]
    return targets


def take_desktop(since=""):
    with _LOCK:
        return [n for n in DESKTOP_QUEUE if n["id"] > since]
