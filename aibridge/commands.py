"""Slash commands: quick controls you type in the message box (/pause, /claude fix the login bug, /undo, ...).

HUMAN ONLY. They are run only for what YOU send from the app or a paired phone. An AI writing "/stop" in a message is
just text: nothing here ever looks at what the AIs say. Start a message with // to send a literal "/..." to the AIs.

Your own commands (Settings > Your commands) are text templates with {blanks}: "/fix {file}" -> "Find and fix the bug in
{file}. Explain what was wrong." They live in <data>/commands.json, which the AIs can't write.
"""
import re
import shlex

import core

MAX_CUSTOM = 50
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")
_BLANK = re.compile(r"\{([A-Za-z_][A-Za-z0-9_ -]{0,30})\}")
WHO = {"claude": "claude", "c": "claude", "gpt": "chatgpt", "chatgpt": "chatgpt", "g": "chatgpt", "openai": "chatgpt"}
MODES = {"ask": "approval", "approval": "approval", "askmefirst": "approval", "hands-off": "autonomous", "handsoff": "autonomous",
         "auto": "autonomous", "autonomous": "autonomous", "readonly": "readonly", "read-only": "readonly", "ro": "readonly"}

# name: (usage, what it does). The order here is the order in /help and the autocomplete.
BUILTIN = {
    "claude": ("/claude <message>", "Message Claude only (Claude answers next; ChatGPT sees it but won't reply)"),
    "gpt": ("/gpt <message>", "Message ChatGPT only"),
    "now": ("/now <message>", "Send and cut in right away (same as Send now)"),
    "pause": ("/pause", "Pause after the current turn"),
    "resume": ("/resume", "Carry on"),
    "step": ("/step", "Run one turn, then stay paused"),
    "stop": ("/stop", "End the session"),
    "turns": ("/turns <n> | +<n>", "Set the turn limit, or add turns"),
    "style": ("/style <name> [claude|gpt]", "How they work: chat, discussion, collaboration, review or parallel (+ who builds)"),
    "mode": ("/mode <ask|hands-off|readonly>", "Change the safety mode"),
    "model": ("/model <claude|gpt> <model id>", "Switch a model (from that AI's next turn)"),
    "task": ("/task <text>", "Set the task"),
    "assign": ("/assign <claude|gpt> <job>", "Put a job on the board for one AI"),
    "play": ("/play <playbook>: <blank> | <next blank>", "Start a session from a playbook, e.g. /play Bug hunt: TypeError on line 4"),
    "playbooks": ("/playbooks", "List your playbooks"),
    "report": ("/report", "Show the last session's report card (only you see it)"),
    "decide": ("/decide <#> <claude|gpt|your call>", "Settle a disagreement: pick a side, or say what you want instead"),
    "shot": ("/shot", "Grab a screenshot from a connected app (Roblox Studio or Blender, if you use them) and post it for both AIs"),
    "undo": ("/undo [change #]", "Undo the last file change (or a specific one)"),
    "remember": ("/remember [global] <text>", "Save a note to this chat's memory (or global memory)"),
    "recall": ("/recall [global] <words>", "Search memory (only you see the result)"),
    "new": ("/new [name]", "Start a new chat"),
    "switch": ("/switch <chat name>", "Open another chat"),
    "fork": ("/fork [name]", "Copy this chat (messages, memory, files) into a new one and open it"),
    "skill": ("/skill <name> [claude|gpt]", "Turn a skill on (both AIs, or one); /skill off [name] turns skills off"),
    "skills": ("/skills", "List your skills and which are on"),
    "help": ("/help", "List all commands"),
}


class CommandError(ValueError):
    pass


# --------------------------------------------------------------------------- your own commands
def _path():
    return core.DATA / "commands.json"


def custom():
    out = []
    for c in core._read_json(_path(), []) or []:
        if isinstance(c, dict) and _NAME.match(str(c.get("name", ""))) and c["name"] not in BUILTIN and isinstance(c.get("template"), str):
            out.append({"name": c["name"], "template": c["template"][:4000], "description": str(c.get("description", ""))[:120]})
    return out


def blanks(template):
    seen = []
    for b in _BLANK.findall(template):
        if b not in seen:
            seen.append(b)
    return seen


def save_custom(name, template, description="", old_name=None):
    name = str(name or "").strip().lstrip("/").lower()
    if not _NAME.match(name):
        raise CommandError("a command name is a lowercase word (letters, digits, - or _), like fix or review-pr")
    if name in BUILTIN:
        raise CommandError(f"/{name} is a built-in command; pick another name")
    template = str(template or "").strip()
    if not template:
        raise CommandError("say what the command sends")
    if len(template) > 4000:
        raise CommandError("that's too long for a command (4000 characters max)")
    if template.startswith("/"):
        head = template[1:].split(None, 1)[0].lower() if len(template) > 1 else ""
        if head not in ("claude", "gpt", "now"):
            raise CommandError("a command can start with /claude, /gpt or /now, but can't run other commands")
    cur = [c for c in custom() if c["name"] not in (name, old_name)]
    if len(cur) >= MAX_CUSTOM:
        raise CommandError(f"you can have up to {MAX_CUSTOM} of your own commands")
    cur.append({"name": name, "template": template, "description": str(description or "").strip()[:120]})
    core._write_json(_path(), sorted(cur, key=lambda c: c["name"]))
    return custom()


def delete_custom(name):
    cur = custom()
    if not any(c["name"] == name for c in cur):
        raise CommandError(f"there's no command /{name}")
    core._write_json(_path(), [c for c in cur if c["name"] != name])
    return custom()


def expand(c, rest):
    """Fill a template's {blanks} from what you typed after the command. Several blanks: one word each (use "quotes"
    for a phrase), the last blank takes everything left over."""
    names = blanks(c["template"])
    if not names:
        return c["template"] + (("\n\n" + rest) if rest else "")
    try:
        words = shlex.split(rest) if rest else []
    except ValueError:
        words = rest.split()
    if len(words) < len(names):
        raise CommandError(f"/{c['name']} needs: " + " ".join("{" + n + "}" for n in names))
    vals = words[:len(names) - 1] + [" ".join(words[len(names) - 1:])]
    out = c["template"]
    for n, v in zip(names, vals):
        out = out.replace("{" + n + "}", v)
    return out


def catalog():
    """For the autocomplete and /help."""
    return ([{"name": k, "usage": u, "description": d, "builtin": True} for k, (u, d) in BUILTIN.items()] +
            [{"name": c["name"], "usage": "/" + c["name"] + "".join(" {" + b + "}" for b in blanks(c["template"])),
              "description": c["description"] or c["template"][:80], "builtin": False} for c in custom()])


# --------------------------------------------------------------------------- running them
def is_command(text):
    t = str(text or "")
    return t.startswith("/") and not t.startswith("//") and len(t) > 1 and not t[1].isspace()


def run(app, text, images=None):
    """Run one command typed by the human. Returns {"ok", "note"} plus optionally "show" (text only you see),
    "chat" (the app switched chats) or "sent" (a message went to the AIs)."""
    text = str(text).strip()
    head, _, rest = text[1:].partition(" ")
    name, rest = head.lower(), rest.strip()
    s = app.session
    if name in ("chatgpt",):
        name = "gpt"
    if name == "help":
        lines = [f"{c['usage']}  -  {c['description']}" for c in catalog()]
        return {"ok": True, "show": "Commands (only you can run these; the AIs can't):\n\n" + "\n".join(lines) +
                "\n\nStart a message with // to send a literal /word to the AIs.", "note": ""}
    if name in ("claude", "gpt", "now"):
        if not rest and not images:
            raise CommandError(f"usage: {BUILTIN[name][0]}")
        to = None if name == "now" else WHO[name]
        return send(app, rest, images=images, to=to, now=(name == "now"))
    if name == "pause":
        s.pause()
        return {"ok": True, "note": "Paused. They'll stop after the current turn."}
    if name == "resume":
        s.resume()
        return {"ok": True, "note": "Resumed."}
    if name == "step":
        s.step()
        return {"ok": True, "note": "One turn, then paused."}
    if name == "stop":
        s.stop()
        return {"ok": True, "note": "Stopped."}
    if name == "turns":
        m = re.fullmatch(r"(\+)?(\d{1,3})", rest)
        if not m:
            raise CommandError("usage: /turns 20 (set the limit) or /turns +5 (add 5)")
        n = int(m.group(2))
        if m.group(1):
            s.extend(n)
            return {"ok": True, "note": f"Added {n} turns."}
        if n < 1:
            raise CommandError("the limit has to be at least 1")
        s.set_max_turns(n)
        return {"ok": True, "note": f"Turn limit set to {n}."}
    if name == "style":
        parts = rest.lower().split()
        if not parts or parts[0] not in core.STYLES:
            raise CommandError("usage: /style chat | discussion | collaboration | review | parallel  (add claude or gpt to pick who builds)")
        lead = WHO.get(parts[1]) if len(parts) > 1 else None
        if len(parts) > 1 and not lead:
            raise CommandError("who builds: claude or gpt")
        core.set_style(parts[0], lead)
        return {"ok": True, "note": f"Style: {parts[0]}" + (f", {core.PRETTY[lead]} builds" if lead else "") + "."}
    if name == "mode":
        m = MODES.get(rest.lower().replace(" ", ""))
        if not m:
            raise CommandError("usage: /mode ask | hands-off | readonly")
        core.set_mode(m)
        return {"ok": True, "note": {"approval": "Ask me first: changes wait for your yes.", "autonomous": "Hands-off: no prompts.",
                                     "readonly": "Read-only: nothing can be changed."}[m]}
    if name == "model":
        parts = rest.split()
        if len(parts) != 2 or parts[0].lower() not in WHO:
            raise CommandError("usage: /model claude claude-opus-5-5  or  /model gpt gpt-6-sol")
        who = WHO[parts[0].lower()]
        s.set_models(**{who: parts[1]})
        return {"ok": True, "note": f"{core.PRETTY[who]} switches to {parts[1]} from its next turn."}
    if name == "task":
        if not rest:
            raise CommandError("usage: /task <what they should work on>")
        core.mutate_state(lambda st: st.__setitem__("task", rest[:2000]))
        return {"ok": True, "note": "Task set."}
    if name == "assign":
        parts = rest.split(None, 1)
        if len(parts) < 2 or parts[0].lower() not in WHO:
            raise CommandError("usage: /assign claude <job>  or  /assign gpt <job>")
        who = WHO[parts[0].lower()]
        core.board_add_task("human", parts[1][:200], who, "")
        return {"ok": True, "note": f"On the board for {core.PRETTY[who]}."}
    if name == "decide":
        return decide(app, rest)
    if name == "playbooks":
        import playbooks
        rows = [f"{p['name']}" + (f"  ({', '.join('{' + b + '}' for b in p['blanks'])})" if p["blanks"] else "") + (f"  - {p['description']}" if p.get("description") else "")
                for p in playbooks.listing()]
        return {"ok": True, "show": "Playbooks (start one with /play <name>: <blank> | <next>):\n\n" + ("\n".join(rows) or "none yet"), "note": ""}
    if name == "play":
        import playbooks
        if not rest:
            raise CommandError("usage: /play <playbook>: <blank> | <next blank>   (/playbooks lists them)")
        if s.alive:
            raise CommandError("A session is running. /stop it first, then /play.")
        try:
            pb, values = playbooks.parse_play(rest)
            cfg = playbooks.to_config(pb, values)
            s.start(cfg)
        except playbooks.PlaybookError as e:
            raise CommandError(str(e))
        except ValueError as e:
            raise CommandError(f"Couldn't start '{pb['name']}': {e}")
        return {"ok": True, "note": f"Started '{pb['name']}'." + (f" {s.warning}" if s.warning else "")}
    if name == "report":
        import report
        return {"ok": True, "show": report.as_text(report.latest()), "note": ""}
    if name == "shot":
        return shot(app)
    if name == "undo":
        return undo(rest)
    if name == "remember":
        return remember(rest)
    if name == "recall":
        return recall(rest)
    if name in ("skill", "skills"):
        return skill_cmd(name, rest)
    if name in ("new", "switch", "fork"):
        return chat_cmd(app, name, rest)
    c = next((c for c in custom() if c["name"] == name), None)
    if c:
        msg = expand(c, rest)
        if msg.startswith("/"):  # a template may route to /claude, /gpt or /now (checked when it was saved)
            h2, _, r2 = msg[1:].partition(" ")
            h2 = h2.lower()
            if h2 in ("claude", "gpt", "now"):
                return send(app, r2.strip(), images=images, to=None if h2 == "now" else WHO[h2], now=(h2 == "now"))
        return send(app, msg, images=images)
    near = [k for k in list(BUILTIN) + [c["name"] for c in custom()] if k.startswith(name[:2])][:3]
    raise CommandError(f"/{name} isn't a command." + (f" Did you mean {', '.join('/' + n for n in near)}?" if near else
                       " Type /help for the list.") + " To send it to the AIs as text, start with //")


def send(app, text, images=None, to=None, now=False):
    import images as images_mod
    s = app.session
    text = (text or "")[:8000]
    ids = [str(i) for i in (images or [])][:images_mod.MAX_PER_MESSAGE]
    for i in ids:
        if images_mod.meta(i)["by"] != "human":
            raise CommandError("you can only attach images you uploaded")
    if not text and not ids:
        raise CommandError("say something first")
    imgs = [{"id": i, "name": images_mod.meta(i)["name"]} for i in ids] or None
    core.append_transcript("human", text or "(sent an image)", images=imgs, to=to)
    if to:
        s.direct_next(to)
    note = s.send_now(to=[to] if to else None) if now else s.nudge()
    return {"ok": True, "note": note, "sent": True}


def decide(app, rest):
    m = re.fullmatch(r"#?(\d+)\s+(.+)", rest, re.S)
    if not m:
        open_ = [d for d in core.load_state().get("disagreements", []) if d["status"] in ("open", "needs_human")]
        hint = ("Open: " + ", ".join(f"#{d['id']} {d['topic']}" for d in open_)) if open_ else "There are no open disagreements."
        raise CommandError("usage: /decide 2 claude  |  /decide 2 gpt  |  /decide 2 <what you want instead>. " + hint)
    did, what = int(m.group(1)), m.group(2).strip()
    head, _, note = what.partition(" ")
    pick = WHO.get(head.lower().rstrip(":,"))
    try:
        if pick:
            core.decide_disagreement(did, pick, note)
        else:
            core.decide_disagreement(did, "custom", what)
    except ValueError as e:
        raise CommandError(str(e))
    return {"ok": True, "note": f"Decided #{did}. " + app.session.nudge()}


def shot(app):
    import images
    import connectors
    data, src = None, ""
    if "roblox" in connectors.enabled():
        try:
            _, imgs = connectors.call_tool("roblox", "screen_capture", {})
            if imgs:
                data, src = imgs[0], "Roblox Studio"
        except connectors.ConnectorError as e:
            err = str(e)
    if data is None and app.session.blender:
        try:
            data, src = core.take_screenshot("human").read_bytes(), "Blender"
        except Exception as e:  # Blender not open, add-on off, ...
            err = str(e)
    if data is None:
        raise CommandError("Nothing to screenshot: turn on the Roblox Studio connector (with Studio open) or turn Blender on in Settings > Connectors and start a session."
                           + (f" ({err})" if "err" in locals() else ""))
    m = images.add("human", data, name=f"{src.lower().replace(' ', '_')}_shot", note=f"/shot from {src}")
    core.append_transcript("human", f"(screenshot of {src})", images=[{"id": m["id"], "name": m["name"]}])
    return {"ok": True, "note": f"Posted a screenshot of {src}. " + app.session.nudge(), "sent": True}


def undo(rest):
    if rest:
        if not rest.lstrip("#").isdigit():
            raise CommandError("usage: /undo  or  /undo 12 (a change number from Files > Recent changes)")
        vid = int(rest.lstrip("#"))
    else:
        cand = [v for v in core.file_versions(limit=300) if v["status"] == "done" and not v.get("undone_by") and not v.get("undoes")
                and v["by"] in ("claude", "chatgpt")]
        if not cand:
            raise CommandError("there's no AI file change to undo")
        vid = cand[0]["id"]
    v = next((x for x in core.file_versions(limit=300) if x["id"] == vid), None)
    core.undo_file_version(vid, "human")
    return {"ok": True, "note": f"Undid change #{vid}" + (f" to {v['path']}." if v else ".")}


def _mem_pid(scope_global):
    import memory
    if scope_global:
        return "global"
    import chats
    c = chats.active() if chats.enabled() else None
    pid = (c or {}).get("memory") or memory.active_project()
    if not pid:
        raise CommandError("this chat has no memory yet")
    return pid


def remember(rest):
    import memory
    g = rest.lower().startswith("global ")
    text = rest[7:].strip() if g else rest
    if not text:
        raise CommandError("usage: /remember <note>  or  /remember global <note>")
    pid = _mem_pid(g)
    title = re.sub(r"\s+", " ", text)[:60]
    try:
        memory.apply(pid, "human", "PROJECT.md", op="append", heading="From you", title=title, content=text[:2000],
                     status="verified", evidence="told by the human", agent=False)
    except memory.MemoryError_ as e:
        raise CommandError(str(e))
    return {"ok": True, "note": "Saved to " + ("global memory." if g else "this chat's memory.")}


def recall(rest):
    import memory
    g = rest.lower().startswith("global ")
    q = rest[7:].strip() if g else rest
    if not q:
        raise CommandError("usage: /recall <words>")
    hits = memory.search(_mem_pid(g), q)
    if not hits:
        return {"ok": True, "show": f"Nothing in {'global' if g else 'this chat'}'s memory matches \"{q}\".", "note": ""}
    lines = []
    for h_ in hits[:8]:
        lines.append(f"[{h_.get('file', '')}{(' ' + h_['id']) if h_.get('id') else ''}] {h_.get('snippet', '').strip()}")
    return {"ok": True, "show": f"Memory matches for \"{q}\":\n\n" + "\n\n".join(lines), "note": ""}


def chat_cmd(app, name, rest):
    import chats
    if not chats.enabled():
        raise CommandError("chats aren't set up")
    if name == "new":
        cur = chats.active() or {}
        cid = chats.create_chat(rest or "New chat", cur.get("project", ""))
    elif name == "fork":
        cur = chats.active()
        if app.session.alive and not app.session.paused:
            raise CommandError("pause the session first (/pause), then fork")
        cid = chats.fork_chat(cur["id"], rest or (cur["name"] + " (copy)"))
    else:
        if not rest:
            raise CommandError("usage: /switch <chat name>")
        ov = chats.overview()["chats"]
        exact = [c for c in ov if c["name"].lower() == rest.lower()]
        part = [c for c in ov if rest.lower() in c["name"].lower()]
        pick = exact or part
        if not pick:
            raise CommandError(f"no chat called \"{rest}\"")
        if len(pick) > 1 and not exact:
            raise CommandError("more than one chat matches: " + ", ".join(c["name"] for c in pick[:5]))
        cid = pick[0]["id"]
    r = app.switch_chat(cid, pause=True)
    if not r.get("ok"):
        return {"ok": True, "note": r.get("note", "Waiting for the current turn to finish; try again in a moment."), "chat": cid}
    label = {"new": "New chat started.", "fork": "Copied this chat and opened the copy.", "switch": "Switched chats."}[name]
    return {"ok": True, "note": label, "chat": cid}


def skill_cmd(name, rest):
    import skills
    try:
        if name == "skills" or not rest:
            lst, on = skills.listing(), skills.active()
            if not lst:
                return {"ok": True, "show": "No skills yet. Add some in Settings > Skills (import from Claude Code or Codex, upload a .zip, or write one).", "note": ""}
            rows = []
            for k in lst:
                who = [core.PRETTY[a] for a in skills.AIS if k["id"] in on[a]]
                rows.append(f"{k['name']}  ({k['id']})" + (f"  ON for {' and '.join(who)}" if who else "") + ("  [auto-load]" if k.get("auto") else "")
                            + f"\n    {k['description'][:160]}")
            return {"ok": True, "show": "Your skills:\n\n" + "\n".join(rows) + "\n\n/skill <name> turns one on, /skill <name> claude (or gpt) for one AI, /skill off to turn them off.", "note": ""}
        words = rest.split()
        if words[0].lower() == "off":
            target = " ".join(words[1:])
            who = AIS_ALL
            if words[-1].lower() in WHO and len(words) > 1:
                who, target = (WHO[words[-1].lower()],), " ".join(words[1:-1])
            sid = skills.find(target) if target else None
            label = skills.turn_off(sid, who)
            return {"ok": True, "note": f"Skill off: {label}." if label != "none" else "No skills were on."}
        who = AIS_ALL
        if len(words) > 1 and words[-1].lower() in WHO:
            who, words = (WHO[words[-1].lower()],), words[:-1]
        if len(words) > 1 and words[-1].lower() in ("both", "all"):
            words = words[:-1]
        sid = skills.find(" ".join(words))
        nm = skills.turn_on(sid, who)
        return {"ok": True, "note": f"Skill on: {nm} for {' and '.join(core.PRETTY[a] for a in who)} (from their next turn)."}
    except skills.SkillError as e:
        raise CommandError(str(e))


AIS_ALL = ("claude", "chatgpt")
