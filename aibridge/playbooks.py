"""Playbooks: saved session setups you start in one go ("Bug hunt", "Build a feature", ...).

A playbook is a New session form, saved: the task (with {blanks} you fill in when you start it), style, safety mode,
turn limit, who starts, and optionally which AI sits in each seat (and its model and name) and which skills are on.
Start one from New session (the Playbook picker), or with  /play <name>: <blank value> | <next blank value>  in the app
or Discord.

Stored in <data>/playbooks.json (app-owned; the AIs can't write it). Three starters are added the first time and can be
edited or deleted like any other.
"""
import re

import core

MAX = 40
_BLANK = re.compile(r"\{([A-Za-z_][A-Za-z0-9_ -]{0,30})\}")
MODES = ("approval", "autonomous", "readonly")
SEAT_KEYS = ("claude_provider", "claude_backend", "claude_model", "claude_name", "gpt_provider", "gpt_backend", "gpt_model", "gpt_name")
STARTERS = [
    {"name": "Build a feature", "description": "One builds, the other reviews; you OK every change.",
     "setup": {"task": "Build {feature}. Plan it on the board first, then build it and test it before calling it done.",
               "style": "collaboration", "lead": "claude", "mode": "approval", "turns": 20, "first": "claude"}},
    {"name": "Bug hunt", "description": "Paste an error; they find the cause before touching code.",
     "setup": {"task": "Find and fix this bug: {error}. Explain the cause first, then make the smallest fix and say how you checked it.",
               "style": "review", "lead": "claude", "mode": "approval", "turns": 12, "first": "claude"}},
    {"name": "Brainstorm", "description": "Ideas only, nothing gets changed.",
     "setup": {"task": "Brainstorm {topic}. Give five ideas each, poke holes in each other's, then agree on the best two.",
               "style": "discussion", "mode": "readonly", "turns": 8, "first": "claude"}},
]


class PlaybookError(ValueError):
    pass


def _path():
    return core.DATA / "playbooks.json"


def _load():
    d = core._read_json(_path(), None)
    if d is None:  # first time: the starters
        d = {"items": [], "seeded": False}
    if not d.get("seeded"):
        for i, st in enumerate(STARTERS):
            if not any(x["name"].lower() == st["name"].lower() for x in d["items"]):
                d["items"].append({"id": f"pb_{i + 1}", "name": st["name"], "description": st["description"], "setup": clean_setup(st["setup"]),
                                   "starter": True})
        d["seeded"] = True
        core._write_json(_path(), d)
    return d


def listing():
    return [dict(p, blanks=blanks(p["setup"].get("task", "") + " " + p["setup"].get("opener", ""))) for p in _load()["items"]]


def blanks(text):
    seen = []
    for b in _BLANK.findall(text or ""):
        if b not in seen:
            seen.append(b)
    return seen


def clean_setup(s):
    """Only what a New session form holds, checked."""
    s = dict(s or {})
    out = {}
    for k in ("task", "opener", "task_claude", "task_chatgpt"):
        if s.get(k):
            out[k] = str(s[k]).strip()[:4000]
    if s.get("style"):
        if s["style"] not in core.STYLES:
            raise PlaybookError("style must be one of " + ", ".join(core.STYLES))
        out["style"] = s["style"]
    for k in ("lead", "first"):
        if s.get(k):
            if s[k] not in core.OTHER:
                raise PlaybookError(f"{k} must be claude or chatgpt")
            out[k] = s[k]
    if s.get("mode"):
        if s["mode"] not in MODES:
            raise PlaybookError("mode must be approval, autonomous or readonly")
        out["mode"] = s["mode"]
    if s.get("turns") not in (None, ""):
        try:
            out["turns"] = max(1, min(500, int(s["turns"])))
        except (TypeError, ValueError):
            raise PlaybookError("turns must be a number")
    if s.get("budget_usd") not in (None, ""):
        try:
            out["budget_usd"] = max(0.0, float(s["budget_usd"]))
        except (TypeError, ValueError):
            raise PlaybookError("budget must be a number")
    for k in ("blender", "start_paused", "fresh", "memory_autoload"):
        if k in s and s[k] is not None:
            out[k] = bool(s[k])
    for k in SEAT_KEYS:
        v = str(s.get(k) or "").strip()
        if v:
            if k.endswith("_backend") and v not in ("api", "subscription") and not re.match(r"^ai:[a-z][a-z0-9_]{1,23}$", v):
                raise PlaybookError(f"{k} must be api, subscription or ai:<profile>")
            if k.endswith("_provider") and v not in ("claude", "openai"):
                raise PlaybookError(f"{k} must be claude or openai")
            if k.endswith("_model") and (len(v) > 100 or not all(c.isalnum() or c in "._:/-" for c in v)):
                raise PlaybookError(f"'{v[:40]}' doesn't look like a model id")
            out[k] = v[:100]
    if s.get("skills"):
        out["skills"] = [str(x)[:64] for x in list(s["skills"])[:20]]
    return out


def find(name_or_id):
    q = str(name_or_id or "").strip().lower()
    items = _load()["items"]
    exact = [p for p in items if p["id"] == q or p["name"].lower() == q]
    if exact:
        return exact[0]
    pre = [p for p in items if p["name"].lower().startswith(q)] if q else []
    if len(pre) == 1:
        return pre[0]
    if len(pre) > 1:
        raise PlaybookError(f"'{name_or_id}' matches more than one playbook: " + ", ".join(p["name"] for p in pre))
    raise PlaybookError(f"no playbook called '{name_or_id}'. You have: " + ", ".join(p["name"] for p in items))


def save(name, setup, description="", pid=None):
    """Add, or replace (same id, or same name)."""
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:40]
    if not name:
        raise PlaybookError("give the playbook a name")
    if ":" in name or "|" in name:
        raise PlaybookError("a playbook name can't contain : or |")
    setup = clean_setup(setup)
    d = _load()
    cur = next((p for p in d["items"] if (pid and p["id"] == pid) or (not pid and p["name"].lower() == name.lower())), None)
    if pid and not cur:
        raise PlaybookError("that playbook doesn't exist any more")
    clash = next((p for p in d["items"] if p["name"].lower() == name.lower() and (not cur or p["id"] != cur["id"])), None)
    if clash:
        raise PlaybookError(f"there's already a playbook called '{clash['name']}'")
    if cur:
        cur.update(name=name, description=str(description or "").strip()[:160], setup=setup)
        cur.pop("starter", None)
        out = cur
    else:
        if len(d["items"]) >= MAX:
            raise PlaybookError(f"you can keep up to {MAX} playbooks")
        n = 1
        while any(p["id"] == f"pb_{n}" for p in d["items"]):
            n += 1
        out = {"id": f"pb_{n}", "name": name, "description": str(description or "").strip()[:160], "setup": setup}
        d["items"].append(out)
    core._write_json(_path(), d)
    return out


def delete(pid):
    d = _load()
    if not any(p["id"] == pid for p in d["items"]):
        raise PlaybookError("no such playbook")
    d["items"] = [p for p in d["items"] if p["id"] != pid]
    core._write_json(_path(), d)


def fill(text, values):
    """Put values into {blanks}, in order of first appearance (or by name if values is a dict)."""
    names = blanks(text)
    if isinstance(values, dict):
        vals = {k: str(v) for k, v in values.items()}
    else:
        vals = {n: str(v) for n, v in zip(names, values or [])}
    return _BLANK.sub(lambda m: vals.get(m.group(1), m.group(0)), text or "")


def to_config(pb, values=None):
    """A playbook (+ blank values) -> the config Session.start takes. Unfilled blanks are refused."""
    s = dict(pb["setup"])
    for k in ("task", "opener", "task_claude", "task_chatgpt"):
        if s.get(k):
            s[k] = fill(s[k], values)
    left = blanks(" ".join(str(s.get(k, "")) for k in ("task", "opener", "task_claude", "task_chatgpt")))
    if left:
        raise PlaybookError("fill in: " + ", ".join("{" + b + "}" for b in left))
    s.setdefault("fresh", False)
    return s


def parse_play(rest):
    """'/play Bug hunt: TypeError on line 4 | app.py' -> (playbook, [values])."""
    name, _, vals = str(rest or "").partition(":")
    pb = find(name)
    values = [v.strip() for v in vals.split("|")] if vals.strip() else []
    return pb, values
