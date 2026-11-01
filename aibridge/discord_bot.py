"""Discord: AI Bridge's notifications and remote control, as one bot that only takes orders from you.

What it does once it's linked to a channel:
  - approvals show up as cards with Approve / Deny buttons (and the change, unless you switch previews off)
  - the conversation is mirrored into one thread per chat; anything you type in that thread goes to the AIs,
    and AI Bridge's own commands work there too (/turns +5, /claude fix the login bug, /undo ...)
  - Discord slash commands: /status /pause /resume /stop /emergency /undo /say /cmd, and /link to pair
  - notifications (finished, waiting, stuck, limits, lost connection) ping you; quiet hours post without a ping

Safety:
  - Only the Discord account that linked it (with a one-time code from Settings) is obeyed. Anyone else gets a polite no;
    messages from anyone else are ignored.
  - The bot token lives in .env (AIBRIDGE_DISCORD_BOT_TOKEN). It is never sent to the page, the AIs or their tools.
  - What passes through Discord: the chat (if mirroring is on) and change previews (if previews are on). Keys never do:
    everything is passed through core.redact() first.

The brain (Bridge) doesn't know about discord.py: it talks to a "transport" with send / edit / thread. DiscordService is
the real transport (discord.py, on its own thread); the self-test uses a fake one.
"""
import asyncio
import io
import json
import os
import re
import secrets
import threading
import time

import core
import notify

TOKEN_ENV = "AIBRIDGE_DISCORD_BOT_TOKEN"
COLORS = {"claude": 0xD97757, "chatgpt": 0x10A37F, "human": 0x5865F2, "system": 0x6B7280, "ok": 0x2EA043, "bad": 0xDA3633, "warn": 0xD29922}
EMBED_MAX = 3900  # Discord allows 4096 in an embed's text; leave room
PREVIEW_MAX = 1400
MIRROR_PER_TICK = 15
LINK_TTL = 600
# View channel, send messages, embed links, attach files, read history, add reactions, create public threads, send in threads
PERMS = 1024 | 2048 | 16384 | 32768 | 65536 | 64 | (1 << 35) | (1 << 38)
DEFAULTS = {"enabled": True, "owner_id": "", "owner_name": "", "guild_id": "", "channel_id": "", "channel_name": "",
            "threads": {}, "mirror": True, "previews": True}
SLASH = {"status": "What's happening: turn, who's working, spend, waiting approvals",
         "tasks": "The task board: who's doing what, what's blocked", "summary": "What the AIs did lately (files changed, tools used)",
         "report": "The last session's report card",
         "pause": "Pause after the current turn", "resume": "Carry on", "stop": "End the session",
         "emergency": "Emergency stop: nothing new runs, waiting approvals are denied",
         "undo": "Undo the last file change (or a given change #)", "say": "Send a message to both AIs",
         "cmd": "Run any AI Bridge command, e.g.  turns +5   or   claude fix the login bug"}


def _path():
    return core.DATA / "discord.json"


def config():
    c = json.loads(json.dumps(DEFAULTS))
    saved = core._read_json(_path(), {}) or {}
    c.update({k: v for k, v in saved.items() if k in DEFAULTS})
    if not isinstance(c["threads"], dict):
        c["threads"] = {}
    return c


def save_config(c):
    core._write_json(_path(), {k: c[k] for k in DEFAULTS})


def has_token():
    return bool(os.getenv(TOKEN_ENV))


def token_ok(t):
    return bool(re.match(r"^[A-Za-z0-9_\-]{18,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{20,}$", t or ""))


def invite_url(app_id):
    return (f"https://discord.com/oauth2/authorize?client_id={app_id}&scope=bot%20applications.commands&permissions={PERMS}"
            if app_id else "")


def chunks(text, n=EMBED_MAX):
    """Split long text on paragraph / line breaks so nothing is cut mid-line if it can be helped."""
    text, out = text or "", []
    while len(text) > n:
        cut = max(text.rfind("\n\n", 0, n), text.rfind("\n", 0, n))
        cut = cut if cut > n // 2 else n
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return out + [text] if text or not out else out


def _chat():
    """(id, name) of the chat that's open in AI Bridge right now."""
    try:
        import chats
        if chats.enabled():
            c = chats.active()
            if c:
                return c["id"], c["name"]
    except Exception:
        pass
    return "main", "AI Bridge"


def _name(who):
    if who == "human":
        return "You"
    if who == "system":
        return "AI Bridge"
    return core.PRETTY.get(who, who)


class Bridge:
    """Everything the bot decides, without any Discord code (so it can be tested with a fake transport)."""

    def __init__(self, app, transport):
        self.app, self.t = app, transport
        self.lock = threading.RLock()
        self.link = None  # {"code", "until", "tries"}
        self.cursor = {}  # chat id -> how much of its transcript has been mirrored
        self.cards = {}  # (chat id, approval id) -> (where, message id)
        self.dcards = {}  # (chat id, disagreement id) -> (where, message id)
        self.seen_report = {}  # chat id -> last report card posted
        self.outbox = []  # notifications waiting to be posted
        self.from_discord = []  # messages you typed in Discord: already there, so not echoed back
        self.last_error = ""

    # ---- linking ----------------------------------------------------------------------------------
    def linked(self):
        c = config()
        return bool(c["owner_id"] and c["channel_id"])

    def is_owner(self, uid):
        o = config()["owner_id"]
        return bool(o) and str(uid) == o

    def new_link_code(self):
        self.link = {"code": f"{secrets.randbelow(10 ** 6):06d}", "until": time.time() + LINK_TTL, "tries": 0}
        return self.link["code"]

    def link_with(self, uid, uname, guild_id, channel_id, channel_name, code):
        lk = self.link
        if not lk or time.time() > lk["until"]:
            return "There's no link code waiting. In AI Bridge open Settings > Discord, press Link, and use the new code."
        if not secrets.compare_digest(str(code or "").strip(), lk["code"]):
            lk["tries"] += 1
            if lk["tries"] >= 5:
                self.link = None
                return "Wrong code too many times, so it was cancelled. Make a new one in Settings > Discord."
            return "That code doesn't match. Check Settings > Discord in AI Bridge."
        c = config()
        c.update(owner_id=str(uid), owner_name=str(uname)[:60], guild_id=str(guild_id or ""), channel_id=str(channel_id),
                 channel_name=str(channel_name or "")[:80], threads={})
        save_config(c)
        self.link, self.cards = None, {}
        with self.lock:
            self.cursor = {}
        where = f"#{channel_name}" if guild_id else "this DM"
        return (f"Linked! I only take orders from you ({uname}). I'll post in {where}" + (", with one thread per chat." if guild_id else ".") +
                " Type in a chat's thread to talk to the AIs; /status, /pause, /stop and the rest work anywhere I can see.")

    def unlink(self):
        c = config()
        c.update(owner_id="", owner_name="", guild_id="", channel_id="", channel_name="", threads={})
        save_config(c)
        self.cards, self.outbox = {}, []

    # ---- where things go --------------------------------------------------------------------------
    def dest(self):
        """The active chat's thread (made the first time it's needed), or the linked channel itself in a DM."""
        c = config()
        if not c["guild_id"]:
            return c["channel_id"]
        cid, name = _chat()
        tid = c["threads"].get(cid)
        if tid:
            return tid
        try:
            tid = self.t.thread(c["channel_id"], name)
        except Exception as e:  # no permission to make threads: use the channel, and say why in Settings
            self.last_error = f"Couldn't make a thread ({type(e).__name__}: {e}); posting in the channel instead."
            return c["channel_id"]
        c = config()
        c["threads"][cid] = tid
        save_config(c)
        return tid

    def chat_for(self, channel_id):
        """Which chat a thread belongs to (None = the main channel)."""
        for cid, tid in config()["threads"].items():
            if str(tid) == str(channel_id):
                return cid
        return None

    # ---- the 1-second tick: approvals, the conversation, notifications ------------------------------
    def tick(self):
        if not self.linked():
            return
        with self.lock:
            for step in (self._approvals, self._disagreements, self._mirror, self._reports, self._notes):
                try:
                    step()
                except Exception as e:  # one failure (Discord hiccup) mustn't stop the others
                    self.last_error = f"{step.__name__[1:]}: {type(e).__name__}: {e}"[:300]

    def _mirror(self):
        cid, _ = _chat()
        msgs = core._read_json(core.TRANSCRIPT_PATH, [])
        n = self.cursor.get(cid)
        if n is None or n > len(msgs) or not config()["mirror"]:
            self.cursor[cid] = len(msgs)  # first look at this chat (or mirroring off): start from now, don't replay history
            return
        for m in msgs[n:n + MIRROR_PER_TICK]:
            self.cursor[cid] += 1
            if m.get("speaker") == "system" and str(m.get("text", "")).startswith(("Report card #", "📋 Report card")):
                continue  # the full report card is posted on its own
            if m.get("speaker") == "human" and m.get("text") in self.from_discord:
                self.from_discord.remove(m["text"])
                continue
            for p in self.render_message(m):
                self.t.send(self.dest(), p)

    def render_message(self, m):
        who = m.get("speaker", "system")
        text = core.redact(str(m.get("text", "")))
        name = _name(who) + (" (on the PC)" if who == "human" else "")
        if who == "human" and m.get("to") in core.PRETTY:
            name += f" → {core.PRETTY[m['to']]} only"
        foot = " · ".join(x for x in (m.get("model"), m.get("ts")) if x)
        files = []
        for im in (m.get("images") or [])[:4]:
            try:
                import images
                meta, data = images.read(im["id"])
                if len(data) <= 8_000_000:
                    files.append((f"{meta['id']}.{meta['ext']}", data))
            except Exception:
                pass
        parts = chunks(text)
        out = []
        for i, part in enumerate(parts):
            out.append({"embeds": [{"author": name if i == 0 else "", "description": part or "​", "color": COLORS.get(who, COLORS["system"]),
                                    "footer": foot if i == len(parts) - 1 else ""}],
                        "files": files if i == len(parts) - 1 else []})
        return out

    def _approvals(self):
        cid, _ = _chat()
        c = config()
        for r in core._read_json(core.APPROVALS_PATH, []):
            key = (cid, r["id"])
            if r["status"] == "pending" and key not in self.cards:
                where = self.dest()
                self.cards[key] = (where, self.t.send(where, self.render_card(r, c, cid)))
            elif r["status"] != "pending" and key in self.cards:
                where, mid = self.cards.pop(key)
                self.t.edit(where, mid, self.render_card(r, c, cid))

    def render_card(self, r, c, cid):
        who, detail = r.get("by", ""), core.redact(str(r.get("detail") or ""))
        final = r["status"] != "pending"
        lines = []
        if r.get("warning"):
            lines.append("⚠ " + core.redact(r["warning"]))
        files = []
        if detail and c["previews"]:
            lang = "diff" if re.search(r"^--- .*\n\+\+\+ ", detail, re.M) else ""
            shown = detail if len(detail) <= PREVIEW_MAX else detail[:PREVIEW_MAX].rsplit("\n", 1)[0] + "\n…"
            lines.append(f"```{lang}\n{shown.replace('```', '`​``')}\n```")
            if len(detail) > PREVIEW_MAX and not final:
                files.append((f"approval-{r['id']}.{'diff' if lang else 'txt'}", detail.encode("utf-8")))
                lines.append("Full change attached.")
        elif detail:
            lines.append("_Previews are off, so the change isn't shown here. It's on the PC (AI Bridge)._")
        status = {"approved": ("✅ Approved", COLORS["ok"]), "denied": ("❌ Denied", COLORS["bad"]),
                  "expired": ("⌛ Timed out (counted as no)", COLORS["system"])}.get(r["status"], ("⏳ Waiting for you", COLORS["warn"]))
        if final:
            lines.append(f"**{status[0]}**" + (f" at {r['decided']}" if r.get("decided") else ""))
        p = {"embeds": [{"title": f"{_name(who)} wants to: {core.redact(str(r.get('action', '')))}"[:250], "description": "\n".join(lines)[:EMBED_MAX],
                         "color": status[1], "footer": f"Request #{r['id']} · {r.get('ts', '')}"}],
             "files": files, "buttons": [] if final else [(f"ab:yes:{cid}:{r['id']}:{r.get('key', '')}", "Approve", "success"),
                                                          (f"ab:no:{cid}:{r['id']}:{r.get('key', '')}", "Deny", "danger")],
             "ping": not final}
        return p

    def _reports(self):
        import report
        cid, _ = _chat()
        r = report.latest()
        rid = r["id"] if r else 0
        if cid not in self.seen_report:
            self.seen_report[cid] = rid  # first look at this chat: don't post old reports
            return
        if rid > self.seen_report[cid]:
            self.seen_report[cid] = rid
            self.t.send(self.dest(), {"embeds": [{"title": f"📋 Report card #{rid}: {r['ending']}",
                                                  "description": core.redact(report.as_text(r).split("\n", 1)[-1])[:EMBED_MAX],
                                                  "color": COLORS["bad"] if r["kind"] in ("error", "emergency") else COLORS["ok"],
                                                  "footer": f"{r['turns']} turn(s) · {report.duration(r['minutes'])} · " + (f"${r['cost']:.2f}" if r["cost"] else "no API cost")}]})

    def _disagreements(self):
        cid, _ = _chat()
        for d in core.load_state().get("disagreements", []):
            key = (cid, d["id"])
            if d["status"] == "needs_human" and key not in self.dcards:
                where = self.dest()
                self.dcards[key] = (where, self.t.send(where, self.render_disagreement(d, cid)))
            elif d["status"] in ("decided", "settled") and key in self.dcards:
                where, mid = self.dcards.pop(key)
                self.t.edit(where, mid, self.render_disagreement(d, cid))

    def render_disagreement(self, d, cid):
        final = d["status"] in ("decided", "settled")
        lines = []
        for who in ("claude", "chatgpt"):
            side = d["sides"].get(who)
            if side:
                lines.append(f"**{_name(who)}:** {core.redact(side['position'])[:900]}" + (f"\n_why:_ {core.redact(side['why'])[:600]}" if side.get("why") else ""))
        if final:
            dec = d.get("decision") or {}
            lines.append("**✅ " + ("Your call: " + core.redact(dec.get("text", "")) if dec.get("pick") == "custom" else
                                     f"Going with {_name(dec.get('pick'))}" + (f" ({core.redact(dec['text'])})" if dec.get("text") else "")) + "**"
                         if dec.get("by") == "human" else f"**✅ Settled between them: going with {_name(dec.get('pick'))}.**")
        else:
            lines.append(f"Something else? Type  `/decide {d['id']} <your call>`  here.")
        return {"embeds": [{"title": f"⚖ They disagree: {core.redact(d['topic'])}"[:250], "description": "\n\n".join(lines)[:EMBED_MAX],
                            "color": COLORS["ok"] if final else COLORS["warn"], "footer": f"Disagreement #{d['id']}"}],
                "buttons": [] if final else [(f"dg:{who}:{cid}:{d['id']}:{d['key']}", f"Go with {_name(who)}", "primary")
                                             for who in ("claude", "chatgpt") if who in d["sides"]],
                "ping": not final}

    def notify_sink(self, event, title, body, key, quiet):
        if not self.linked():
            raise RuntimeError("Discord isn't linked yet (Settings > Discord)")
        if str(key).startswith(("approval-", "disagree-")):
            return  # the card is the notification
        with self.lock:
            self.outbox.append({"event": event, "title": title, "body": body, "quiet": quiet})
            del self.outbox[:-30]

    def _notes(self):
        while self.outbox:
            n = self.outbox.pop(0)
            icon = {"finished": "✅", "waiting": "🙋", "error": "🚨", "limit": "⏳", "stuck": "🔁", "progress": "📈"}.get(n["event"], "🔔")
            self.t.send(self.dest(), {"content": f"{icon} **{n['title']}**\n{n['body']}"[:1900], "ping": not n["quiet"]})

    # ---- from Discord -------------------------------------------------------------------------------
    def refuse(self):
        o = config()["owner_name"]
        return f"This AI Bridge only takes orders from {o}." if o else "This AI Bridge isn't linked yet. Its owner links it with /link."

    def on_button(self, uid, custom_id):
        if not self.is_owner(uid):
            return self.refuse()
        if str(custom_id).startswith("dg:"):
            return self._disagree_button(custom_id)
        m = re.fullmatch(r"ab:(yes|no):([^:]+):(\d+):([0-9a-f]{16})", custom_id or "")
        if not m:
            return "That button is from an older version of this card; nothing was done. Answer it in AI Bridge."
        cid, name = _chat()
        if m.group(2) != cid:
            return f"That request is from another chat. '{name}' is open in AI Bridge now; open that chat to answer it."
        # the exact request this card was made for: same number AND same one-time key (numbers restart each session)
        req = next((r for r in core._read_json(core.APPROVALS_PATH, []) if r["id"] == int(m.group(3))), None)
        if not req or not secrets.compare_digest(str(req.get("key", "")), m.group(4)):
            return "That request no longer exists (a new session started since), so nothing was done."
        if req["status"] != "pending" or not core.decide_approval(req["id"], m.group(1) == "yes"):
            return "That one was already answered (or timed out)."
        threading.Thread(target=self.tick, daemon=True).start()  # update the card right away
        return "Approved ✓" if m.group(1) == "yes" else "Denied ✗"

    def _disagree_button(self, custom_id):
        m = re.fullmatch(r"dg:(claude|chatgpt):([^:]+):(\d+):([0-9a-f]{16})", custom_id or "")
        if not m:
            return "I don't know that button."
        cid, name = _chat()
        if m.group(2) != cid:
            return f"That's from another chat. '{name}' is open in AI Bridge now."
        d = next((x for x in core.load_state().get("disagreements", []) if x["id"] == int(m.group(3))), None)
        if not d or not secrets.compare_digest(str(d.get("key", "")), m.group(4)):
            return "That disagreement no longer exists (a new session started since), so nothing was done."
        try:
            core.decide_disagreement(d["id"], m.group(1))
        except ValueError as e:
            return f"⚠ {e}"
        note = self.app.session.nudge()
        threading.Thread(target=self.tick, daemon=True).start()
        return f"Going with {_name(m.group(1))}. {note}"

    def on_message(self, uid, channel_id, text, attachments=()):
        """A message typed in the linked channel or a chat thread. -> None (ignore) | ("react", emoji) | ("reply", text)"""
        import commands
        import images
        if not self.is_owner(uid):
            return None  # other people in the server: not our business, and no replies to spam the channel
        c = config()
        cid_here = self.chat_for(channel_id)
        if str(channel_id) != c["channel_id"] and cid_here is None:
            return None
        cid, name = _chat()
        if cid_here and cid_here != cid:
            other = next((x["name"] for x in self._chats() if x["id"] == cid_here), "that chat")
            return ("reply", f"This thread is for '{other}', but '{name}' is open in AI Bridge. Type  /switch {other}  to open it.")
        text = (text or "").strip()
        try:
            ids = []
            for fname, data in list(attachments)[:images.MAX_PER_MESSAGE]:
                ids.append(images.add("human", data, fname)["id"])
            if text.startswith("/") and not text.startswith("//"):
                res = commands.run(self.app, text, images=ids)
                out = res.get("show") or res.get("note") or "Done."
                return ("reply", core.redact(out)[:1900])
            if text.startswith("//"):
                text = text[1:]
            if not text and not ids:
                return None
            self.from_discord.append((text[:8000] or "(sent an image)"))
            del self.from_discord[:-20]
            res = commands.send(self.app, text, images=ids)
            note = res.get("note") or ""
            return ("react", "✅") if note in ("", "Sent.", "Sent. They'll see it on the next turn.") else ("reply", "✅ " + note[:1900])
        except Exception as e:  # CommandError, ImageError, ValueError: say what's wrong
            return ("reply", f"⚠ {core.redact(str(e))[:1800]}")

    def _chats(self):
        try:
            import chats
            return chats.overview()["chats"]
        except Exception:
            return []

    def on_slash(self, uid, name, arg=""):
        import commands
        if not self.is_owner(uid):
            return self.refuse()
        s = self.app.session
        try:
            if name == "status":
                return self.status_text()
            if name == "tasks":
                return self.tasks_text()
            if name == "summary":
                return self.summary_text()
            if name == "report":
                import report
                return core.redact(report.as_text(report.latest()))[:1900]
            if name == "emergency":
                s.emergency_stop()
                return "⛔ Emergency stop. Nothing new will run and waiting approvals were denied. Start a new session or use /cmd turns +5 to carry on."
            if name in ("pause", "resume", "stop"):
                return commands.run(self.app, "/" + name)["note"]
            if name == "undo":
                return commands.run(self.app, ("/undo " + str(arg)).strip())["note"]
            if name == "say":
                if not str(arg).strip():
                    return "Say something first."
                self.from_discord.append(str(arg)[:8000])
                return commands.send(self.app, str(arg))["note"] or "Sent."
            if name == "cmd":
                res = commands.run(self.app, "/" + str(arg).strip().lstrip("/"))
                return core.redact(res.get("show") or res.get("note") or "Done.")[:1900]
        except Exception as e:
            return f"⚠ {core.redact(str(e))[:1800]}"
        return "I don't know that one."

    def tasks_text(self):
        board = core.load_state().get("board", [])
        if not board:
            return "No tasks on the board yet."
        ids = {str(t["id"]): t.get("status") for t in board}
        order = {"doing": 0, "blocked": 1, "todo": 2, "done": 3}
        rows = sorted(board, key=lambda t: (order.get(t.get("status"), 9), t["id"]))
        done = sum(t.get("status") == "done" for t in board)
        lines = [f"**Board**: {done}/{len(board)} done"] + [_board_line(t, ids) for t in rows if t.get("status") != "done"][:25]
        if done:
            lines.append(f"…and {done} done")
        return core.redact("\n".join(lines))[:1900]

    def summary_text(self):
        st = core.load_state()
        ch = st.get("changes", [])[-8:]
        acts = list(core.ACTIVITY_LOG)
        counts = {}
        for a in acts:
            k = (core.PRETTY.get(a["agent"], a["agent"]), a["tool"])
            counts[k] = counts.get(k, 0) + 1
        lines = ["**Lately**"]
        try:
            vers = core.file_versions(limit=8)
        except Exception:
            vers = []
        if vers:
            lines.append("Files changed: " + ", ".join(f"#{v['id']} {v['path']} ({core.PRETTY.get(v.get('by'), v.get('by', ''))})" for v in vers))
        if counts:
            top = sorted(counts.items(), key=lambda kv: -kv[1])[:8]
            lines.append("Tools used: " + ", ".join(f"{who} {tool} ×{n}" for (who, tool), n in top))
        dec = st.get("decisions", [])[-3:]
        if dec:
            lines.append("Decisions: " + " | ".join(str(d.get("text", d))[:120] for d in dec))
        if len(lines) == 1:
            lines.append("Nothing yet this session.")
        return core.redact("\n".join(lines))[:1900]

    def status_text(self):
        s = self.app.session
        _, chat = _chat()
        u = core.usage_summary()
        pend = core.pending_approvals()
        who = [core.PRETTY.get(n, n) for n in (s.currents or ([s.current] if s.current else []))]
        lines = [f"**{chat}**: {s.status()}" + (f", turn {s.turn}/{s.max_turns}" if s.turn or s.alive else ""),
                 "Seats: " + "  ·  ".join(f"{core.PRETTY[n]} ({s.models.get(n, '')})" for n in ("claude", "chatgpt")),
                 f"Safety: {({'approval': 'Ask me first', 'autonomous': 'Hands-off', 'readonly': 'Read-only'}).get(core.MODE, core.MODE)}"]
        if who:
            lines.append("Working now: " + ", ".join(who))
        lines.append(f"Spend: ${u['total']:.2f}" + (f" of ${u['limit']:.2f}" if u["limit"] else ""))
        if pend:
            lines.append(f"Waiting for you: {len(pend)} approval(s) (cards above)")
        dis = [d for d in core.load_state().get("disagreements", []) if d["status"] == "needs_human"]
        if dis:
            lines.append("They disagree, your call: " + ", ".join(f"#{d['id']} {d['topic']}" for d in dis))
        if s.offline_seats():
            lines.append("Lost connection: " + ", ".join(core.PRETTY[n] for n in s.offline_seats()))
        if s.warning:
            lines.append("Note: " + core.redact(s.warning)[:300])
        if s.error:
            lines.append("Error: " + core.redact(s.error)[:300])
        return "\n".join(lines)


def _ago(ts):
    return f" ({ts})" if ts else ""


def _board_line(t, ids):
    dot = {"todo": "⬜", "doing": "🟨", "blocked": "🟥", "done": "✅"}.get(t.get("status"), "▫")
    who = core.PRETTY.get(t.get("owner"), "You" if t.get("owner") == "human" else "unassigned")
    raw = t.get("depends_on") or []
    deps = [str(d) for d in (raw if isinstance(raw, list) else str(raw).replace(" ", "").split(",")) if str(d)]
    wait = [d for d in deps if d in ids and ids[d] != "done"]
    return f"{dot} #{t['id']} {t.get('title', '')[:90]}  ·  {who}" + (f"  ·  waiting on #{', #'.join(wait)}" if wait else "")


class DiscordService:
    """The real transport: runs discord.py on its own thread, plus the brain's 1-second tick."""

    def __init__(self, app):
        self.app = app
        self.bridge = Bridge(app, self)
        self.state, self.detail, self.bot_name, self.app_id = "off", "", "", ""
        self.loop = self.client = self.thread = None
        self._stop = threading.Event()
        self._ticker = None

    # ---- lifecycle --------------------------------------------------------------------------------------
    def start(self):
        if self.thread and self.thread.is_alive():
            return
        if not has_token() or not config()["enabled"]:
            self.state, self.detail = "off", ""
            return
        try:
            import discord  # noqa: F401
        except ImportError:
            self.state, self.detail = "error", "The discord.py package isn't installed. Run:  pip install discord.py"
            return
        self._stop.clear()
        self.state, self.detail = "connecting", ""
        notify.SINKS["discord"] = self.bridge.notify_sink
        self.thread = threading.Thread(target=self._run, args=(os.getenv(TOKEN_ENV),), daemon=True, name="discord-bot")
        self.thread.start()
        if not self._ticker or not self._ticker.is_alive():
            self._ticker = threading.Thread(target=self._tick_loop, daemon=True, name="discord-tick")
            self._ticker.start()

    def stop(self, wait=True):
        self._stop.set()
        notify.SINKS.pop("discord", None)
        if self.loop and self.client:
            try:
                asyncio.run_coroutine_threadsafe(self.client.close(), self.loop).result(10)
            except Exception:
                pass
        if wait and self.thread:
            self.thread.join(10)
        self.state = "off" if self.state != "error" else self.state

    def restart(self):
        self.stop()
        self.state, self.detail = "off", ""
        self.start()

    def info(self):
        c = config()
        return {"state": self.state, "detail": self.detail, "bot": self.bot_name, "has_token": has_token(),
                "invite": invite_url(self.app_id), "linked": self.bridge.linked(), "owner": c["owner_name"],
                "channel": c["channel_name"], "dm": bool(c["channel_id"] and not c["guild_id"]),
                "mirror": c["mirror"], "previews": c["previews"], "enabled": c["enabled"],
                "link_code": self.bridge.link["code"] if self.bridge.link and time.time() < self.bridge.link["until"] else "",
                "link_expires": int(self.bridge.link["until"]) if self.bridge.link else 0, "last_error": self.bridge.last_error}

    def _tick_loop(self):
        while not self._stop.is_set():
            if self.state == "online":
                self.bridge.tick()
            self._stop.wait(1.0)

    # ---- transport (called from the tick thread, never from the bot's own loop) ------------------------
    def _call(self, make, timeout=30):
        if not self.loop or self.state != "online":
            raise RuntimeError("the Discord bot isn't online")
        return asyncio.run_coroutine_threadsafe(make(), self.loop).result(timeout)

    async def _channel(self, cid):
        return self.client.get_channel(int(cid)) or await self.client.fetch_channel(int(cid))

    def send(self, where, p):
        async def go():
            ch = await self._channel(where)
            msg = await ch.send(**self.kwargs(p))
            return str(msg.id)
        return self._call(go)

    def edit(self, where, mid, p):
        async def go():
            ch = await self._channel(where)
            kw = self.kwargs(p, editing=True)
            await ch.get_partial_message(int(mid)).edit(**kw)
        return self._call(go)

    def thread(self, channel_id, name):
        async def go():
            import discord
            ch = await self._channel(channel_id)
            t = await ch.create_thread(name=(name or "AI Bridge")[:90], type=discord.ChannelType.public_thread, auto_archive_duration=10080)
            return str(t.id)
        return self._call(go)

    def kwargs(self, p, editing=False):
        """Our plain payload dict -> discord.py send/edit arguments."""
        import discord
        owner = config()["owner_id"]
        content = p.get("content") or None
        if p.get("ping") and owner and not editing:
            content = f"<@{owner}> " + (content or "")
        embeds = []
        for e in p.get("embeds") or []:
            em = discord.Embed(title=e.get("title") or None, description=e.get("description") or None, color=e.get("color"))
            if e.get("author"):
                em.set_author(name=e["author"][:250])
            if e.get("footer"):
                em.set_footer(text=e["footer"][:200])
            embeds.append(em)
        kw = {"content": content, "embeds": embeds,
              "allowed_mentions": discord.AllowedMentions(everyone=False, roles=False,
                                                          users=[discord.Object(int(owner))] if p.get("ping") and owner else False)}
        if "buttons" in p:
            view = None
            if p["buttons"]:
                view = discord.ui.View(timeout=None)
                for cid, label, style in p["buttons"]:
                    view.add_item(discord.ui.Button(label=label, custom_id=cid, style=getattr(discord.ButtonStyle, style)))
            kw["view"] = view
        if editing:
            kw.pop("allowed_mentions", None)
            if content is None:
                kw.pop("content")
        elif p.get("files"):
            kw["files"] = [discord.File(io.BytesIO(data), filename=name) for name, data in p["files"][:10]]
        return kw

    # ---- the bot itself ---------------------------------------------------------------------------------
    def build(self, discord, app_commands):
        """Make the client and register its commands and events (separate from connecting, so it can be checked offline)."""
        try:  # no voice here: skip discord.py's "PyNaCl / davey is not installed" warnings on every start
            discord.VoiceClient.warn_nacl = False
            discord.VoiceClient.warn_dave = False
        except Exception:
            pass
        intents = discord.Intents.default()
        intents.message_content = True  # to read what you type in the thread ("Message Content Intent" in the developer portal)
        client = discord.Client(intents=intents)
        tree = app_commands.CommandTree(client)
        br, svc = self.bridge, self

        async def off_loop(fn, *a):
            return await asyncio.get_running_loop().run_in_executor(None, fn, *a)

        @tree.command(name="link", description="Link this channel to AI Bridge (use the code from Settings > Discord)")
        @app_commands.describe(code="The 6-digit code AI Bridge shows you")
        async def link(inter, code: str):
            ch = inter.channel
            parent = getattr(ch, "parent", None) or ch
            msg = await off_loop(br.link_with, inter.user.id, str(inter.user), inter.guild_id, parent.id, getattr(parent, "name", "DM"), code)
            await inter.response.send_message(msg, ephemeral=True)

        def simple(name):
            async def cb(inter):
                await inter.response.defer(ephemeral=True, thinking=True)
                await inter.followup.send((await off_loop(br.on_slash, inter.user.id, name))[:1900], ephemeral=True)
            return app_commands.Command(name=name, description=SLASH[name], callback=cb)

        for n in ("status", "tasks", "summary", "report", "pause", "resume", "stop", "emergency"):
            tree.add_command(simple(n))

        @tree.command(name="undo", description=SLASH["undo"])
        @app_commands.describe(change="Change number (leave empty for the latest)")
        async def undo(inter, change: int = 0):
            await inter.response.defer(ephemeral=True, thinking=True)
            await inter.followup.send((await off_loop(br.on_slash, inter.user.id, "undo", str(change or "")))[:1900], ephemeral=True)

        @tree.command(name="say", description=SLASH["say"])
        async def say(inter, message: str):
            await inter.response.defer(ephemeral=True, thinking=True)
            await inter.followup.send((await off_loop(br.on_slash, inter.user.id, "say", message))[:1900], ephemeral=True)

        @tree.command(name="cmd", description=SLASH["cmd"])
        @app_commands.describe(command="An AI Bridge command without the slash, e.g. turns +5")
        async def cmd(inter, command: str):
            await inter.response.defer(ephemeral=True, thinking=True)
            await inter.followup.send((await off_loop(br.on_slash, inter.user.id, "cmd", command))[:1900], ephemeral=True)

        @client.event
        async def on_ready():
            svc.bot_name, svc.app_id = str(client.user), str(client.application_id or "")
            if not getattr(svc, "_synced", False):
                try:
                    await tree.sync()
                    svc._synced = True
                except Exception as e:
                    svc.detail = f"Couldn't register the / commands: {type(e).__name__}: {e}"
            svc.state = "online"

        @client.event
        async def on_message(msg):
            if msg.author.bot:
                return
            files = []
            for a in msg.attachments[:4]:
                if (a.content_type or "").startswith("image/") and a.size <= 15_000_000:
                    files.append((a.filename, await a.read()))
            res = await off_loop(br.on_message, msg.author.id, msg.channel.id, msg.content, files)
            if not res:
                return
            kind, val = res
            if kind == "react":
                await msg.add_reaction(val)
            else:
                await msg.reply(val[:1900], mention_author=False, allowed_mentions=discord.AllowedMentions.none())

        @client.event
        async def on_interaction(inter):
            if inter.type == discord.InteractionType.component and str((inter.data or {}).get("custom_id", "")).startswith(("ab:", "dg:")):
                reply = await off_loop(br.on_button, inter.user.id, inter.data["custom_id"])
                await inter.response.send_message(reply, ephemeral=True)

        return client, tree

    def _run(self, token):
        import discord
        from discord import app_commands
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        try:
            self.client, _ = self.build(discord, app_commands)
            loop.run_until_complete(self.client.start(token))
            if self.state != "error":
                self.state = "off"
        except discord.LoginFailure:
            self.state, self.detail = "error", "Discord refused the bot token. Copy a fresh one (Developer Portal > your app > Bot > Reset Token)."
        except discord.PrivilegedIntentsRequired:
            self.state, self.detail = "error", ("Turn on \"Message Content Intent\" for the bot (Developer Portal > your app > Bot > Privileged Gateway "
                                                "Intents), then press Reconnect.")
        except Exception as e:
            self.state, self.detail = "error", core.redact(f"{type(e).__name__}: {e}")[:300]
        finally:
            try:
                loop.close()
            except Exception:
                pass
            self.loop = None
