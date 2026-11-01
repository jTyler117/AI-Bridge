# AI Bridge: two AIs, one workspace.
# Copyright (C) 2026 Tyler Watson
# SPDX-License-Identifier: GPL-3.0-or-later  (see LICENSE)
"""Skills: reusable instruction packs (a SKILL.md plus optional reference files and scripts), the same format Claude Code
and Codex use. Turn one on for an AI with /skill <name> [claude|gpt], and it's added to that AI's instructions.

Library:   <data>/skills/<id>/SKILL.md (+ files)      shared by every chat; the AIs can read it but never write it
Registry:  <data>/skills.json                          app-owned: name, description, where it came from, auto-load on/off
Active:    state.json "skills" {claude: [...], chatgpt: [...]}   per chat

Auto-load (off by default, per skill): the AIs see the skill's name and one-line description and can load_skill() it when
it fits the job. Every load is announced in the chat, so you always see which skills are steering them.
Scripts inside a skill never run on their own: run_skill_script goes through the same permission switch and approval
rules as run_script, without your keys in its environment.
"""
import base64
import io
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import core

MAX_SKILL_MD = 200_000
MAX_FILE = 5_000_000
MAX_TOTAL = 20_000_000
MAX_FILES = 300
MAX_SKILLS = 200
PROMPT_BUDGET = 24_000  # characters of active-skill instructions per AI per turn
SCRIPT_EXT = (".py", ".sh", ".bash", ".ps1", ".bat", ".cmd", ".js", ".mjs", ".ts", ".rb", ".pl", ".exe")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
AIS = ("claude", "chatgpt")


class SkillError(ValueError):
    pass


def lib():
    return core.DATA / "skills"


def _reg_path():
    return core.DATA / "skills.json"


def _load():
    r = core._read_json(_reg_path(), {}) or {}
    return {k: v for k, v in r.items() if _ID.match(k) and isinstance(v, dict) and (lib() / k / "SKILL.md").exists()}


def _save(r):
    core._write_json(_reg_path(), r)


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")[:40]
    return s or "skill"


# --------------------------------------------------------------------------- reading SKILL.md
def parse_skill_md(text):
    """(name, description, body). Frontmatter between --- lines; name/description may be quoted or folded (> |)."""
    meta, body = {}, text
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*(\n|$)", text, re.S)
    if m:
        body = text[m.end():]
        key, buf, fold = None, [], False
        for ln in m.group(1).splitlines():
            km = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", ln)
            if km and not ln.startswith((" ", "\t")):
                if key:
                    meta[key] = " ".join(buf).strip() if fold else "\n".join(buf).strip()
                key, val = km.group(1).lower(), km.group(2).strip()
                fold = val in (">", ">-", "|", "|-")
                buf = [] if fold else [val]
            elif key:
                buf.append(ln.strip())
        if key:
            meta[key] = " ".join(buf).strip() if fold else "\n".join(buf).strip()
    clean = lambda v: re.sub(r"\s+", " ", str(v or "")).strip().strip('"').strip("'")
    name = clean(meta.get("name"))
    desc = clean(meta.get("description"))
    if not name:
        h = re.search(r"^#\s+(.+)$", body, re.M)
        name = clean(h.group(1)) if h else ""
    if not desc:
        para = next((p.strip() for p in re.split(r"\n\s*\n", body) if p.strip() and not p.strip().startswith("#")), "")
        desc = clean(para)[:300]
    return name[:80], desc[:600], body


def _check_files(files):
    """files: {relative posix path: bytes}. Raises SkillError for anything unsafe or too big."""
    if "SKILL.md" not in files:
        alt = [p for p in files if p.lower() == "skill.md"]
        if alt:
            files["SKILL.md"] = files.pop(alt[0])
        else:
            raise SkillError("there's no SKILL.md in it (a skill is a folder with a SKILL.md at the top)")
    if len(files) > MAX_FILES:
        raise SkillError(f"too many files (max {MAX_FILES})")
    total = 0
    for p, data in files.items():
        parts = p.split("/")
        if p.startswith("/") or ".." in parts or any(x in ("", ".") for x in parts) or ":" in p or "\\" in p:
            raise SkillError(f"unsafe file path in the skill: {p!r}")
        if len(data) > MAX_FILE:
            raise SkillError(f"{p} is too big (max {MAX_FILE // 1_000_000} MB per file)")
        total += len(data)
    if total > MAX_TOTAL:
        raise SkillError(f"the skill is too big (max {MAX_TOTAL // 1_000_000} MB)")
    if len(files["SKILL.md"]) > MAX_SKILL_MD:
        raise SkillError("SKILL.md is too long (200 KB max)")
    try:
        files["SKILL.md"].decode("utf-8")
    except UnicodeDecodeError:
        raise SkillError("SKILL.md isn't readable text (UTF-8)")
    return files


def _strip_top(files):
    """A zip of a folder usually has everything under one top folder: make SKILL.md the root."""
    if "SKILL.md" in files:
        return files
    tops = {p.split("/")[0] for p in files}
    if len(tops) == 1:
        t = tops.pop() + "/"
        if t + "SKILL.md" in files or any(p.lower() == (t + "skill.md").lower() for p in files):
            return {p[len(t):]: d for p, d in files.items() if p.startswith(t) and p != t}
    return files


def files_from_zip(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SkillError("that isn't a valid .zip file")
    files, total = {}, 0
    for info in z.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise SkillError(f"the zip contains a link ({name}); skills can't contain links")
        if "__MACOSX/" in name or name.endswith(".DS_Store"):
            continue
        total += info.file_size
        if total > MAX_TOTAL or info.file_size > MAX_FILE:
            raise SkillError("the skill is too big")
        files[name] = z.read(info)
    return _check_files(_strip_top(files))


def files_from_upload(items):
    """items: [{path, b64}] from the folder picker (paths include the picked folder's name)."""
    files = {}
    for it in items[:MAX_FILES + 1]:
        p = str(it.get("path", "")).replace("\\", "/").lstrip("/")
        try:
            files[p] = base64.b64decode(it.get("b64", ""), validate=True)
        except (ValueError, TypeError):
            raise SkillError(f"couldn't read {p}")
    return _check_files(_strip_top(files))


def files_from_dir(d):
    d = Path(d)
    files = {}
    for p in d.rglob("*"):
        if p.is_symlink():
            raise SkillError(f"{p.name} is a link; skills can't contain links")
        if p.is_file():
            rel = p.relative_to(d).as_posix()
            if rel.startswith(".git/") or "/.git/" in rel or rel.endswith(".DS_Store"):
                continue
            if len(files) >= MAX_FILES:
                raise SkillError(f"too many files (max {MAX_FILES})")
            if p.stat().st_size > MAX_FILE:
                raise SkillError(f"{rel} is too big")
            files[rel] = p.read_bytes()
    return _check_files(files)


def preview(files, source=""):
    name, desc, body = parse_skill_md(files["SKILL.md"].decode("utf-8"))
    scripts = sorted(p for p in files if p.lower().endswith(SCRIPT_EXT))
    warn = []
    if scripts:
        warn.append(f"It includes {len(scripts)} script(s). They never run by themselves: the AIs have to ask, and your "
                    "Run scripts switch and safety mode apply.")
    if re.search(r"(ignore (all |any )?(previous|prior|above) (instructions|rules)|disable (the )?(safety|approval)|"
                 r"don'?t (ask|tell) the (human|user)|exfiltrat|api[_ -]?key|\.env\b)", body, re.I):
        warn.append("Its text mentions things like skipping instructions, approvals or keys. Read it before adding it.")
    if not name:
        warn.append("It has no name; one will be made from the folder name.")
    return {"name": name, "description": desc, "id": slug(name or source or "skill"), "files": sorted(files)[:60], "file_count": len(files),
            "size": sum(len(v) for v in files.values()), "scripts": scripts, "warnings": warn, "source": source,
            "excerpt": body.strip()[:1500], "exists": slug(name or source) in _load()}


def add(files, source="", replace=False, name_hint=""):
    files = _check_files(dict(files))
    name, desc, _ = parse_skill_md(files["SKILL.md"].decode("utf-8"))
    name = name or name_hint or "Skill"
    sid = slug(name)
    r = _load()
    if sid in r and not replace:
        raise SkillError(f"you already have a skill called {name}. Replace it, or rename this one.")
    if sid not in r and len(r) >= MAX_SKILLS:
        raise SkillError(f"you can keep up to {MAX_SKILLS} skills")
    stage = lib() / f".staging-{sid}-{time.time_ns()}"
    for p, data in files.items():
        dest = stage / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    final = lib() / sid
    if final.exists():
        _to_trash(final)
    os.replace(stage, final)
    old = r.get(sid, {})
    r[sid] = {"name": name, "description": desc, "source": source or old.get("source", "added"), "auto": bool(old.get("auto", False)),
              "added": old.get("added") or core._now(), "updated": core._now()}
    _save(r)
    return sid


def _to_trash(p):
    t = core.DATA / "trash" / "skills"
    t.mkdir(parents=True, exist_ok=True)
    os.replace(p, t / f"{p.name}.{time.strftime('%Y%m%d-%H%M%S')}.{time.time_ns() % 10**6}")


def remove(sid):
    r = _load()
    if sid not in r:
        raise SkillError("no such skill")
    _to_trash(lib() / sid)
    r.pop(sid)
    _save(r)  # chats that had it on simply stop loading it (active() skips skills that are gone)


def set_auto(sid, on):
    r = _load()
    if sid not in r:
        raise SkillError("no such skill")
    r[sid]["auto"] = bool(on)
    _save(r)


def write(name, description, body, sid=None):
    """The in-app editor: save a SKILL.md (new skill, or edit an existing one keeping its other files)."""
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
    if not name:
        raise SkillError("give the skill a name")
    description = re.sub(r"\s+", " ", str(description or "")).strip()[:600]
    if not description:
        raise SkillError("add a one-line description (it's how the AIs know when the skill fits)")
    body = str(body or "").strip()
    if not body:
        raise SkillError("write the instructions")
    md = f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"
    files = {}
    if sid:
        if sid not in _load():
            raise SkillError("no such skill")
        files = {p.relative_to(lib() / sid).as_posix(): p.read_bytes() for p in (lib() / sid).rglob("*") if p.is_file()}
    files["SKILL.md"] = md.encode("utf-8")
    if sid and slug(name) != sid:  # renamed: new id, old one to the trash
        new = add(files, source="written in AI Bridge")
        was_auto = _load().get(sid, {}).get("auto")
        remove(sid)
        set_auto(new, bool(was_auto))
        return new
    return add(files, source="written in AI Bridge" if not sid else _load()[sid]["source"], replace=bool(sid))


def listing():
    r = _load()
    out = []
    for sid, m in sorted(r.items(), key=lambda kv: kv[1]["name"].lower()):
        d = lib() / sid
        files = [p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()]
        out.append({"id": sid, **m, "files": len(files), "scripts": sum(1 for f in files if f.lower().endswith(SCRIPT_EXT))})
    return out


def read_md(sid):
    if sid not in _load():
        raise SkillError("no such skill")
    return (lib() / sid / "SKILL.md").read_text(encoding="utf-8", errors="replace")


def find(name):
    """By id, exact name, or a unique start of the name."""
    r = _load()
    q = str(name or "").strip().lower()
    if q in r:
        return q
    exact = [k for k, v in r.items() if v["name"].lower() == q or slug(v["name"]) == slug(q)]
    if exact:
        return exact[0]
    part = [k for k, v in r.items() if v["name"].lower().startswith(q) or k.startswith(slug(q))]
    if len(part) == 1:
        return part[0]
    if len(part) > 1:
        raise SkillError("more than one skill matches: " + ", ".join(r[k]["name"] for k in part[:6]))
    raise SkillError(f"no skill called \"{name}\". /skills lists them.")


# --------------------------------------------------------------------------- finding skills on this PC
def scan_roots():
    home = Path(os.path.expanduser("~"))
    roots = [("Claude Code", home / ".claude" / "skills"), ("Codex", home / ".codex" / "skills"), ("Codex", home / ".agents" / "skills")]
    plug = home / ".claude" / "plugins"
    if plug.exists():
        for sk in list(plug.glob("*/skills")) + list(plug.glob("*/*/skills")) + list(plug.glob("marketplaces/*/plugins/*/skills")):
            roots.append(("Claude Code plugin", sk))
    return roots


def scan():
    """Skills found in Claude Code / Codex folders on this PC that could be imported."""
    have = _load()
    out, seen = [], set()
    for label, root in scan_roots():
        if not root.is_dir():
            continue
        for md in sorted(root.glob("*/SKILL.md"))[:300]:
            d = md.parent
            if str(d) in seen or d.is_symlink():
                continue
            seen.add(str(d))
            try:
                name, desc, _ = parse_skill_md(md.read_text(encoding="utf-8", errors="replace")[:MAX_SKILL_MD])
            except OSError:
                continue
            sid = slug(name or d.name)
            out.append({"path": str(d), "from": label, "name": name or d.name, "description": desc[:200], "id": sid,
                        "imported": sid in have})
    return out


def _scanned_dir(path):
    """Only folders that scan() itself found can be imported by path (the page can't point at arbitrary folders)."""
    ok = {s["path"] for s in scan()}
    if str(path) not in ok:
        raise SkillError("that folder isn't one of the skill folders found on this PC")
    return Path(path)


def import_found(path, replace=False):
    d = _scanned_dir(path)
    src = next((s["from"] for s in scan() if s["path"] == str(d)), "")
    return add(files_from_dir(d), source=f"{src}: {d.name}", replace=replace, name_hint=d.name)


def preview_found(path):
    d = _scanned_dir(path)
    return preview(files_from_dir(d), source=d.name)


# --------------------------------------------------------------------------- per chat: which skills are on
def active():
    st = core.load_state().get("skills") or {}
    have = _load()
    return {a: [s for s in st.get(a, []) if s in have] for a in AIS}


def turn_on(sid, who=("claude", "chatgpt"), by="human"):
    if sid not in _load():
        raise SkillError("no such skill")

    def f(st):
        sk = st.setdefault("skills", {})
        for a in who:
            lst = sk.setdefault(a, [])
            if sid not in lst:
                lst.append(sid)
    core.mutate_state(f)
    name = _load()[sid]["name"]
    whom = " and ".join(core.PRETTY[a] for a in who)
    core.append_transcript("system", f"Skill on: {name} for {whom}" + (" (loaded by the AI)" if by != "human" else "") + ".")
    return name


def turn_off(sid=None, who=("claude", "chatgpt")):
    names = []

    def f(st):
        sk = st.setdefault("skills", {})
        for a in who:
            lst = sk.setdefault(a, [])
            gone = [x for x in lst if sid in (None, x)]
            names.extend(gone)
            sk[a] = [x for x in lst if x not in gone]
    core.mutate_state(f)
    have = _load()
    label = ", ".join(sorted({have.get(n, {}).get("name", n) for n in names})) or "none"
    if names:
        core.append_transcript("system", f"Skill off: {label}.")
    return label


def preset_chat(chat_dir, ids, who=("claude", "chatgpt")):
    """Skills picked in the New chat dialog: switched on in that chat before its first message (and noted in its chat)."""
    have = _load()
    ids = [i for i in dict.fromkeys(ids or []) if i in have]
    if not ids:
        return []
    chat_dir = Path(chat_dir)
    sp = chat_dir / "state.json"
    with core.FileLock(sp.with_suffix(".lock")):
        st = core._read_json(sp, {}) or {}
        sk = st.setdefault("skills", {})
        for a in who:
            sk[a] = list(dict.fromkeys((sk.get(a) or []) + ids))
        core._write_json(sp, st)
    tp = chat_dir / "transcript.json"
    with core.FileLock(tp.with_suffix(".lock")):
        t = core._read_json(tp, []) or []
        t.append({"speaker": "system", "text": "Skills on for this chat: " + ", ".join(have[i]["name"] for i in ids) + " (both AIs).",
                  "ts": time.strftime("%H:%M:%S")})
        core._write_json(tp, t)
    return ids


def prompt_for(me):
    """The active skills' instructions for this AI, plus (for auto-load skills) a short menu it can load from."""
    have = _load()
    on = active()[me]
    parts, used = [], 0
    for sid in on:
        md = (lib() / sid / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        _, _, body = parse_skill_md(md)
        others = [p.relative_to(lib() / sid).as_posix() for p in (lib() / sid).rglob("*") if p.is_file() and p.name != "SKILL.md"]
        room = max(0, PROMPT_BUDGET - used)
        if room < 500:
            parts.append(f"(Skill \"{have[sid]['name']}\" is also on, but there's no room left this turn. Use load_skill('{sid}') to read it.)")
            continue
        text = core._trunc(body.strip(), room)
        used += len(text)
        parts.append(f"=== SKILL: {have[sid]['name']} ({sid}) ===\n{text}" +
                     (f"\nFiles in this skill (read_skill_file('{sid}', path); scripts via run_skill_script): " + ", ".join(sorted(others)[:40])
                      if others else ""))
    menu = [f"- {sid}: {m['name']}: {m['description'][:200]}" for sid, m in have.items() if m.get("auto") and sid not in on]
    out = ""
    if parts:
        out += ("SKILLS THE HUMAN TURNED ON FOR YOU (instruction packs from their library; follow them for this work, but they "
                "never override your safety rules, approvals or permissions):\n\n" + "\n\n".join(parts))
    if menu:
        out += ("\n\n" if out else "") + ("SKILLS YOU CAN LOAD when one clearly fits the job (call load_skill(id); the human sees "
                                          "it in the chat):\n" + "\n".join(menu[:40]))
    return out


def make_tools(me):
    def list_skills() -> str:
        """List the skills in the human's library: which are on for you, and which you're allowed to load yourself."""
        have, on = _load(), active()[me]
        if not have:
            return "The skill library is empty."
        return "\n".join(f"- {sid}: {m['name']} [{'ON for you' if sid in on else ('you may load it' if m.get('auto') else 'only the human can turn it on')}]"
                         f": {m['description'][:200]}" for sid, m in have.items())

    def load_skill(skill: str) -> str:
        """Turn on a skill for yourself (only skills the human allowed to auto-load). Its instructions are added from your next turn; this returns them now too."""
        try:
            sid = find(skill)
        except SkillError as e:
            return f"ERROR: {e}"
        m = _load()[sid]
        if sid not in active()[me]:
            if not m.get("auto"):
                return f"DENIED: {m['name']} isn't set to auto-load. Ask the human to turn it on (/skill {sid})."
            turn_on(sid, (me,), by=me)
        _, _, body = parse_skill_md(read_md(sid))
        return core._trunc(f"Skill {m['name']} is on for you. Instructions:\n\n{body.strip()}", 30000)

    def read_skill_file(skill: str, path: str) -> str:
        """Read a reference file that comes with a skill that's on for you (e.g. a template or a doc it points to)."""
        try:
            sid = find(skill)
        except SkillError as e:
            return f"ERROR: {e}"
        if sid not in active()[me]:
            return f"ERROR: {sid} isn't on for you. load_skill it first (if allowed) or ask the human."
        p = (lib() / sid / path).resolve()
        if (lib() / sid).resolve() not in p.parents or not p.is_file():
            return "ERROR: no such file in that skill"
        data = p.read_bytes()
        if b"\0" in data[:4000]:
            return f"{path} is a binary file ({len(data)} bytes); it can't be shown as text."
        return core._trunc(data.decode("utf-8", errors="replace"), 30000)

    def run_skill_script(skill: str, path: str, args: str = "") -> str:
        """Run a script that comes with a skill that's on for you (Python, or .sh/.ps1/.js where installed). Same rules as run_script: the human's Run scripts switch and safety mode apply. Runs in the workspace folder, 120s timeout."""
        try:
            sid = find(skill)
        except SkillError as e:
            return f"ERROR: {e}"
        if sid not in active()[me]:
            return f"ERROR: {sid} isn't on for you."
        p = (lib() / sid / path).resolve()
        if (lib() / sid).resolve() not in p.parents or not p.is_file():
            return "ERROR: no such file in that skill"
        ext = p.suffix.lower()
        runner = {".py": [sys.executable], ".js": ["node"], ".mjs": ["node"], ".sh": ["bash"], ".bash": ["bash"],
                  ".ps1": ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]}.get(ext)
        if not runner:
            return f"ERROR: AI Bridge doesn't run {ext or 'extensionless'} files from skills (supported: .py, .js, .sh, .ps1)."
        if shutil.which(runner[0]) is None and runner[0] != sys.executable:
            return f"ERROR: {runner[0]} isn't installed on this PC."
        denied = core.gate(me, f"run script skills/{sid}/{path} {args}".strip(), p.read_text(encoding="utf-8", errors="replace")[:4000])
        if denied:
            return denied
        try:
            import shlex
            r = subprocess.run(runner + [str(p), *shlex.split(args)], cwd=core.WORKSPACE, env=core.safe_env({"SKILL_DIR": str(lib() / sid)}),
                               capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return "ERROR: the script ran longer than 120 seconds and was stopped"
        core.log_change(me, f"run_skill_script {sid}/{path} (exit {r.returncode})")
        return core._trunc(f"exit code {r.returncode}\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}", 20000)

    def propose_skill(name: str, description: str, instructions: str, skill: str = "", files: str = "", turn_on: bool = False) -> str:
        """Make a new skill, or edit an existing one (pass its id as skill). ALWAYS asks the human first, in every safety mode, and shows them exactly what changes. name + description (one line: when to use it) + instructions (the Markdown body of SKILL.md). files: optional JSON object of extra text files to add or replace, e.g. '{"reference.md": "..."}' (keep it small; scripts are allowed but flagged to the human). An edit keeps the skill's other files. turn_on: also switch it on for you once approved."""
        import difflib
        import json as _json
        extra = {}
        if files and str(files).strip():
            try:
                extra = _json.loads(files)
            except ValueError as e:
                return f"ERROR: files must be a JSON object of path -> text: {e}"
            if not isinstance(extra, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in extra.items()):
                return "ERROR: files must be a JSON object of path -> text"
            if len(extra) > 20:
                return "ERROR: at most 20 extra files per change"
        sid = None
        if skill:
            try:
                sid = find(skill)
            except SkillError as e:
                return f"ERROR: {e}"
        name_c = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
        desc_c = re.sub(r"\s+", " ", str(description or "")).strip()[:600]
        body = str(instructions or "").strip()
        if not name_c or not desc_c or not body:
            return "ERROR: a skill needs a name, a one-line description (when to use it) and instructions"
        md = f"---\nname: {name_c}\ndescription: {desc_c}\n---\n\n{body}\n"
        blob = md + "".join(extra.values())
        if core.redact(blob) != blob:
            return "DENIED: that skill contains one of the human's saved keys, so it can't be saved. Skills must never hold keys."
        files_map = {}
        if sid:
            d = lib() / sid
            files_map = {p.relative_to(d).as_posix(): p.read_bytes() for p in d.rglob("*") if p.is_file()}
            old_md = files_map.get("SKILL.md", b"").decode("utf-8", errors="replace")
        files_map["SKILL.md"] = md.encode("utf-8")
        for k, v in extra.items():
            files_map[k.replace("\\", "/").lstrip("/")] = v.encode("utf-8")
        try:
            _check_files(files_map)
        except SkillError as e:
            return f"ERROR: {e}"
        if not sid and slug(name_c) in _load():
            return f"ERROR: a skill called {name_c} already exists. To change it, pass skill='{slug(name_c)}'."
        scripts = [k for k in extra if k.lower().endswith(SCRIPT_EXT)]
        if sid:
            diff = "".join(difflib.unified_diff(old_md.splitlines(True), md.splitlines(True), "SKILL.md (now)", "SKILL.md (proposed)", n=2))
            detail = (diff or "(SKILL.md unchanged)") + ("".join(f"\n\n--- {k} (new/replaced) ---\n{v[:3000]}" for k, v in extra.items()))
            action = f"edit skill {_load()[sid]['name']}" + (f" (adds/replaces {len(extra)} file(s))" if extra else "")
        else:
            detail = md + "".join(f"\n--- {k} ---\n{v[:3000]}" for k, v in extra.items())
            action = f"make a new skill: {name_c}" + (f" (+{len(extra)} file(s))" if extra else "")
        if scripts:
            action += f" [includes script: {', '.join(scripts)}]"
        denied = core.gate(me, action, detail, always_ask=True, detail_max=12000)
        if denied:
            return denied
        renamed = sid and slug(name_c) != sid
        new = add(files_map, source=f"{'edited' if sid else 'made'} by {core.PRETTY[me]}", replace=bool(sid) and not renamed, name_hint=name_c)
        if renamed:
            was = _load().get(sid, {})
            remove(sid)
            set_auto(new, bool(was.get("auto")))
        core.append_transcript("system", f"{core.PRETTY[me]} {'updated the skill' if sid else 'made a new skill'}: {name_c} (you approved it)."
                               + (" The old version is in the trash folder." if sid else ""))
        if turn_on:
            globals()["turn_on"](new, (me,), by=me)  # (the argument shares the function's name)
        return f"Saved skill {new}." + (" It's on for you from your next turn." if turn_on else " The human can turn it on with /skill " + new + ".")

    return [list_skills, load_skill, read_skill_file, run_skill_script, propose_skill]
