# AI Bridge: two AIs, one workspace.
# Copyright (C) 2026 Tyler Watson
# SPDX-License-Identifier: GPL-3.0-or-later  (see LICENSE)
"""Appearance: themes, accent colour, Liquid Glass and custom themes. Shared by every chat and every device (phone remote).

Theme files are DATA ONLY: a name, a few colours, a corner radius and a font from a fixed list. Anything else in an
imported file is dropped, so a shared theme can never run code or change how the app behaves.
"""
import re

import core

BUILTINS = ("dark", "light", "oled", "contrast")
COLOR_KEYS = ("bg", "panel", "panel2", "line", "text", "muted", "accent", "claude", "gpt", "human", "good", "bad", "warn",
              "code", "input", "bar", "raised")
FONTS = {"system": 'system-ui,-apple-system,"Segoe UI",Roboto,sans-serif', "segoe": '"Segoe UI",system-ui,sans-serif',
         "rounded": '"Nunito","Segoe UI Variable Display","Segoe UI",system-ui,sans-serif',
         "mono": '"Cascadia Code",Consolas,"SF Mono",Menlo,monospace', "serif": 'Georgia,"Times New Roman",serif'}
BACKGROUNDS = ("gradient", "solid", "wallpaper")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_CUSTOM = 30
MAX_WALLPAPER = 8_000_000

DEFAULTS = {"theme": "dark", "follow_windows": False, "accent": "", "glass": False, "glass_amount": 50, "glass_background": "gradient",
            "glass_color": "", "glass_grad": ["", "", ""], "reduce_transparency": False, "custom": []}


def _path():
    return core.DATA / "ui.json"


def get():
    s = dict(DEFAULTS)
    s.update({k: v for k, v in (core._read_json(_path(), {}) or {}).items() if k in DEFAULTS})
    s["has_wallpaper"] = bool(wallpaper_file())
    return s


def clean_theme(t):
    """Validate one custom theme. Raises ValueError with a readable message."""
    if not isinstance(t, dict):
        raise ValueError("a theme must be an object")
    name = re.sub(r"\s+", " ", str(t.get("name", ""))).strip()[:40]
    if not name:
        raise ValueError("a theme needs a name")
    colors = {}
    for k, v in (t.get("colors") or {}).items():
        if k in COLOR_KEYS and isinstance(v, str) and _HEX.match(v.strip()):
            colors[k] = v.strip().lower()
    if len(colors) < 4:
        raise ValueError("a theme needs at least a few valid colours (#rrggbb)")
    try:
        radius = max(0, min(24, int(t.get("radius", 12))))
    except (TypeError, ValueError):
        radius = 12
    font = t.get("font") if t.get("font") in FONTS else "system"
    return {"name": name, "colors": colors, "radius": radius, "font": font}


def save(b):
    s = get()
    s.pop("has_wallpaper", None)
    if "save_custom" in b:  # add or replace a custom theme by name
        t = clean_theme(b["save_custom"])
        s["custom"] = [c for c in s["custom"] if c["name"] != t["name"]] + [t]
        if len(s["custom"]) > MAX_CUSTOM:
            raise ValueError(f"you can keep up to {MAX_CUSTOM} custom themes")
    if "theme" in b:
        th = str(b["theme"])
        names = {c["name"] for c in s["custom"]}
        if th not in BUILTINS and not (th.startswith("custom:") and th[7:] in names):
            raise ValueError("unknown theme")
        s["theme"] = th
    for k in ("follow_windows", "glass", "reduce_transparency"):
        if k in b:
            s[k] = bool(b[k])
    if "accent" in b:
        a = str(b["accent"] or "").strip()
        if a and not _HEX.match(a):
            raise ValueError("accent must be a colour like #5b72e0")
        s["accent"] = a.lower()
    if "glass_amount" in b:
        s["glass_amount"] = max(0, min(100, int(b["glass_amount"])))
    if "glass_background" in b:
        if b["glass_background"] not in BACKGROUNDS:
            raise ValueError("background must be gradient, solid or wallpaper")
        s["glass_background"] = b["glass_background"]
    if "glass_grad" in b:  # the three gradient colours; "" = automatic (accent / Claude / ChatGPT colours)
        g = b["glass_grad"]
        if not isinstance(g, list) or len(g) != 3:
            raise ValueError("the gradient needs exactly three colours")
        out = []
        for c in g:
            c = str(c or "").strip().lower()
            if c and not _HEX.match(c):
                raise ValueError("gradient colours must look like #ff66aa")
            out.append(c)
        s["glass_grad"] = out
    if "glass_color" in b and b["glass_color"] in ("", None):
        s["glass_color"] = ""  # automatic: follows the theme's own background
    elif "glass_color" in b:
        if not _HEX.match(str(b["glass_color"])):
            raise ValueError("background colour must look like #1b2440")
        s["glass_color"] = str(b["glass_color"]).lower()
    if "delete_custom" in b:
        name = str(b["delete_custom"])
        s["custom"] = [c for c in s["custom"] if c["name"] != name]
        if s["theme"] == "custom:" + name:
            s["theme"] = "dark"
    core._write_json(_path(), s)
    return get()


# ---- wallpaper for Liquid Glass ------------------------------------------------------------------------------------
def wallpaper_file():
    for ext in ("png", "jpg", "webp", "gif"):
        p = core.DATA / f"wallpaper.{ext}"
        if p.exists():
            return p
    return None


def set_wallpaper(data):
    import images
    kind = images.sniff(data)
    if not kind:
        raise ValueError("only PNG, JPEG, GIF and WebP images can be a wallpaper")
    if len(data) > MAX_WALLPAPER:
        raise ValueError(f"that image is too big (max {MAX_WALLPAPER // 1_000_000} MB)")
    old = wallpaper_file()
    if old:
        old.unlink()
    ext = {"jpeg": "jpg"}.get(kind[0], kind[0])
    (core.DATA / f"wallpaper.{ext}").write_bytes(data)
    return get()
