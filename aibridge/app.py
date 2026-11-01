"""AI Bridge app: a local window where Claude and ChatGPT talk and build together while you watch.

    python aibridge/app.py              # opens the app in your browser
    python aibridge/app.py --window     # own native window (pip install pywebview first)
    python aibridge/app.py --demo       # scripted fake AIs, no API keys or credits needed (try the UI!)

This file is the COORDINATOR. It lives outside both AIs: it runs the turns, owns permissions
(safety mode + approvals), the task board, stop conditions, and the pause/stop buttons. Neither model
can change any of that, only use the tools the coordinator hands them.

Security: the server only listens on 127.0.0.1, rejects foreign Host/Origin headers (DNS rebinding,
other websites), and every /api call needs a random per-launch token that only this app's page knows.
"""
import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import appearance
import chats
import commands
import cli_agents
import core
import images
import memory
import discord_bot
import notify
import playbooks
import providers
import report
import skills

UI_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "ui"
ENV_PATH = core.ROOT / ".env"
MAX_BODY = 1_000_000
CLAUDE_PRESETS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]
GPT_PRESETS = ["gpt-6.1-sol", "gpt-6-sol", "gpt-5.6-sol", "gpt-6-luna", "gpt-5.6-terra", "gpt-5.6-luna"]
_NOT_CHAT = ("embedding", "tts", "whisper", "transcribe", "image", "dall-e", "realtime", "moderation",
             "audio", "live", "sora", "search", "computer-use", "codex", "davinci", "babbage")


def _valid_model(m):
    m = (m or "").strip()
    if not m or len(m) > 100 or not all(c.isalnum() or c in "._:/-" for c in m):
        raise ValueError(f"'{m[:40]}' doesn't look like a model id (letters, digits and . _ : / - only)")
    return m


_OFFLINE_WORDS = ("connection refused", "connection error", "failed to establish", "name or service not known", "getaddrinfo failed",
                  "remote end closed", "connection reset", "connection aborted", "server disconnected", "no route to host", "timed out")


def _is_offline(e, msg):
    """The AI's server stopped answering (not a bad request, not a limit): worth pausing and retrying, not ending."""
    for x in [e, getattr(e, "__cause__", None), getattr(e, "__context__", None)]:
        if x is not None and type(x).__name__ in ("APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout", "ReadTimeout",
                                                  "RemoteProtocolError", "ConnectionRefusedError", "ConnectionResetError", "ConnectionError"):
            return True
    return any(w in msg.lower() for w in _OFFLINE_WORDS)


def list_models(demo=False):
    """Ask each provider what THIS account can actually use. Errors are per-provider, never fatal."""
    if demo:
        return {"claude": {"models": CLAUDE_PRESETS}, "chatgpt": {"models": GPT_PRESETS}, "demo": True}
    out = {}
    try:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise ValueError("no Anthropic key saved yet (Settings)")
        import anthropic
        out["claude"] = {"models": sorted(m.id for m in anthropic.Anthropic().models.list(limit=100).data)}
    except Exception as e:
        out["claude"] = {"models": [], "error": f"{type(e).__name__}: {e}"[:240]}
    try:
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("no OpenAI key saved yet (Settings)")
        import openai
        ids = sorted(m.id for m in openai.OpenAI().models.list())
        out["chatgpt"] = {"models": [i for i in ids if not any(x in i for x in _NOT_CHAT)]}
    except Exception as e:
        out["chatgpt"] = {"models": [], "error": f"{type(e).__name__}: {e}"[:240]}
    return out


# --------------------------------------------------------------------------- demo agents
class DemoAgent:
    """Scripted stand-in for the two models: lets you try every part of the UI for free (and keeps tests free)."""

    def __init__(self, name, delay=1.2, model=None):
        self.name, self.delay, self.n, self.model = name, delay, 0, model

    def turn(self, system, prompt, tools):
        core.preflight(self.name)
        time.sleep(self.delay)
        core.record_usage(self.name, 1800, 350)  # pretend token use so the cost meter has something to show
        n, self.n = self.n, self.n + 1

        def run(tool, **kw):
            return core.run_tool(tools, tool, kw)

        if self.name == "claude":
            if n == 0:
                run("board_add", title="Write the demo README", owner="chatgpt")
                run("board_add", title="Review the README", owner="claude", depends_on="1")
                return ("Hey ChatGPT! Quick plan so we don't trip over each other: you write a README, I review it. "
                        "I put both on the board.")
            if n == 1:
                got = run("read_file", path="demo/README.md")
                if got.startswith("ERROR"):
                    return "I can't find the README, so there's nothing for me to review yet. Shout when it exists."
                run("board_update", task_id=2, status="doing")
                run("board_update", task_id=2, status="done", note="reads clean, no changes needed")
                run("record_decision", text="Ship the README as written.")
                return "Reviewed it, reads clean. I logged the decision to ship it. [DONE]"
            return "Nothing left on my side. [DONE]"
        if n == 0:
            run("board_update", task_id=1, status="doing")
            res = run("write_file", path="demo/README.md",
                      content="# Demo project\n\nWritten by the demo ChatGPT while you watched.\n")
            if not res.startswith("wrote"):
                run("board_update", task_id=1, status="blocked", note="write didn't go through")
                return "That write didn't go through, so I'm parking my task. What do you want me to do instead?"
            run("board_update", task_id=1, status="done")
            return "README is written to demo/README.md. Over to you, Claude."
        return "All good on my end. [DONE]"


# --------------------------------------------------------------------------- the coordinator
class Session:
    def __init__(self, demo=False, demo_delay=1.2):
        self.demo, self.demo_delay = demo, demo_delay
        self.cv = threading.Condition()
        self.thread = None
        self.final = "idle"  # what to report once no loop is alive: idle | finished | stopped | error
        self.paused = False
        self.stop_flag = False
        self.step_n = 0
        self.turn = 0
        self.currents = []
        self.max_turns = 12
        self.current = None
        self.error = ""
        self.warning = ""
        self.blender = False
        self.first = "claude"
        self.order = ["claude", "chatgpt"]
        self.agents = {}
        self.streak = 0
        self.models = {"claude": core.CLAUDE_MODEL, "chatgpt": core.OPENAI_MODEL}
        self.backends = {"claude": "api", "chatgpt": "api"}  # api = per-token keys | subscription = your plan via the official CLI | ai:<profile> = another AI
        self.providers = {"claude": "claude", "chatgpt": "openai"}  # which company's AI sits in each seat (for api / subscription)
        self.names = {"claude": "Claude", "chatgpt": "ChatGPT"}
        self.recovery = None  # set by recover() when the last run died mid-session
        self.file_recovery = None  # file writes that were cut off, and how each one was settled
        self.memory_project = ""  # the project this session's summary is filed under
        self.parking = False  # set while switching to another chat: the loop ends quietly and can be resumed later
        self.resume_at = None  # auto-resume time after a usage limit (sequential styles)
        self._limit_tries = {}
        self.offline = {}  # seat -> tries: an AI whose server stopped answering (sequential styles)
        self._save_lock = threading.Lock()

    @property
    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    def status(self):
        return ("paused" if self.paused else "running") if self.alive else self.final

    # ---- control ----------------------------------------------------------
    def _make_agents(self, cm, gm, backends=None, provs=None):
        """One agent per seat. A seat can hold Claude (API key or plan), ChatGPT (API key or plan) or any AI profile."""
        backends = backends or self.backends
        provs = provs or self.providers
        for k, v in backends.items():
            if v not in ("api", "subscription") and not str(v).startswith("ai:"):
                raise ValueError(f"{k} backend must be api, subscription or ai:<profile>")
        for k, v in provs.items():
            if v not in ("claude", "openai"):
                raise ValueError(f"{k} provider must be claude or openai")
        if self.demo:
            return {n: DemoAgent(n, self.demo_delay, m) for n, m in (("claude", cm), ("chatgpt", gm))}
        agents, missing = {}, []
        for seat, model in (("claude", cm), ("chatgpt", gm)):
            b, prov = backends[seat], provs[seat]
            if str(b).startswith("ai:"):
                try:
                    prof = providers.get(b[3:])
                    caps = providers.preflight(prof, model)  # server up + model there, before anything starts
                    agents[seat] = providers.CompatAgent(prof, model, seat=seat).apply_caps(caps)
                except providers.ProviderError as e:
                    raise ValueError(f"Can't start: {e}")
            elif b == "subscription":
                kind = "claude" if prov == "claude" else "chatgpt"
                found, logged_in, detail = cli_agents.login_status(kind)
                label = "Claude Code" if kind == "claude" else "Codex"
                if not found:
                    raise ValueError(f"'{core.PRETTY[seat]}' is set to your plan, which needs its CLI, and {label} isn't installed. {detail}")
                if not logged_in:
                    raise ValueError(f"{label} isn't signed in. {detail}")
                agents[seat] = cli_agents.make_cli_agent(kind, model, seat=seat)
            else:
                key = "ANTHROPIC_API_KEY" if prov == "claude" else "OPENAI_API_KEY"
                if not os.getenv(key):
                    missing.append(key)
                else:
                    agents[seat] = core.ClaudeAgent(model=model, seat=seat) if prov == "claude" else core.ChatGPTAgent(model=model, seat=seat)
        if missing:
            raise ValueError("Missing API key(s): " + ", ".join(sorted(set(missing))) + ". Add them in Settings, switch that seat to "
                             "your plan or another AI, or try --demo.")
        return agents

    @staticmethod
    def _billing(b):
        if b == "subscription":
            return "subscription"
        if str(b).startswith("ai:"):
            try:
                return "local" if providers.PRESETS[providers.get(b[3:])["preset"]]["local"] else "api"
            except providers.ProviderError:
                return "api"
        return "api"

    def _default_name(self, seat, b, prov, model=""):
        if str(b).startswith("ai:"):
            base = str(model or "").split("/")[-1].split(":")[0][:24]
            if base:
                return base[0].upper() + base[1:]
            try:
                return providers.get(b[3:])["label"]
            except providers.ProviderError:
                return "AI"
        return "Claude" if prov == "claude" else "ChatGPT"

    def start(self, cfg):
        if self.alive:
            raise ValueError("A session is already running. Stop it first.")
        first = cfg.get("first", "claude")
        mode = cfg.get("mode", core.MODE)
        style = cfg.get("style", "chat")
        if first not in core.OTHER:
            raise ValueError("first must be claude or chatgpt")
        if mode not in ("autonomous", "approval", "readonly"):
            raise ValueError("mode must be autonomous, approval or readonly")
        turns = max(1, min(500, int(cfg.get("turns", 12))))
        cm = _valid_model(cfg.get("claude_model") or self.models["claude"])
        gm = _valid_model(cfg.get("gpt_model") or self.models["chatgpt"])
        backends = {"claude": cfg.get("claude_backend") or "api", "chatgpt": cfg.get("gpt_backend") or "api"}
        provs = {"claude": cfg.get("claude_provider") or "claude", "chatgpt": cfg.get("gpt_provider") or "openai"}
        agents = self._make_agents(cm, gm, backends, provs)
        names = {seat: (cfg.get(f"{'claude' if seat == 'claude' else 'gpt'}_name") or "").strip() or self._default_name(seat, backends[seat], provs[seat], cm if seat == "claude" else gm)
                 for seat in ("claude", "chatgpt")}
        self.models, self.backends, self.providers = {"claude": cm, "chatgpt": gm}, backends, provs
        fresh = bool(cfg.get("fresh", True))

        budget = cfg.get("budget_usd")
        if budget not in (None, ""):
            float(budget)  # validate before touching anything
        core.set_approver(None)  # approvals go through the file queue, which the UI answers
        core.halt_clear()  # starting a session is a deliberate human act, so it lifts an old emergency stop
        self.recovery = None
        if fresh:
            core.reset_all()
            images.clear()  # the pictures belonged to the transcript that was just wiped
        core.set_mode(mode)  # after reset_all, which wipes the shared state the tool processes read the mode from
        self.names = core.set_seat_names(names)
        for n, b in backends.items():
            core.set_billing(n, self._billing(b))
        if budget not in (None, ""):
            core.set_budget(limit_usd=budget)
        core.set_style(style, cfg.get("lead", "claude"))
        task = (cfg.get("task") or "").strip()
        if task:
            core.mutate_state(lambda s: s.__setitem__("task", task))
        if style == "parallel":  # optional separate jobs for each AI, put straight on the board with an owner
            for who, key in (("claude", "task_claude"), ("chatgpt", "task_chatgpt")):
                job = (cfg.get(key) or "").strip()
                if job:
                    core.board_add_task("human", job, owner=who)
        if not core.load_transcript(1):
            opener = (cfg.get("opener") or "").strip() or "Hey! Say hi to each other and see where it goes."
            core.append_transcript("human", opener)

        # project memory: which project, and whether to load it into both AIs' instructions for this session
        if "memory_project" in cfg and not chats.enabled():  # with chats, each chat's memory is fixed to that chat
            memory.set_settings(active=str(cfg.get("memory_project") or ""))
        memory.set_settings(session_autoload=None if cfg.get("memory_autoload") is None else bool(cfg.get("memory_autoload")))
        self.memory_project = memory.active_project()

        self.warning = ""
        # Blender is a connector now (Settings > Connectors); a session config can still say on/off explicitly (old playbooks, scripts)
        want_blender = bool(cfg["blender"]) if cfg.get("blender") is not None else bool(app_settings()["blender"])
        if want_blender and not core.blender_alive():
            self.warning = (f"Blender isn't answering on {core.BLENDER_HOST}:{core.BLENDER_PORT} "
                            "(in Blender, turn the MCP add-on server on: Edit > Preferences > Add-ons > MCP, or N-panel > BlenderMCP > Connect), "
                            "so the Blender tools are off for this session. Test it in Settings > Connectors.")
            want_blender = False
        self.blender, self.agents, self.first = want_blender, agents, first
        self.order = [first, core.OTHER[first]]
        self.turn, self.max_turns, self.streak, self.error = 0, turns, 0, ""
        self.stop_flag, self.paused, self.step_n, self.final = False, bool(cfg.get("start_paused")), 0, "idle"
        for sid in cfg.get("skills") or []:  # a playbook can switch skills on (after the reset above, which clears them)
            try:
                skills.turn_on(str(sid))
            except Exception:
                self.warning = (self.warning + " " if self.warning else "") + f"Skill '{sid}' isn't in your library any more, so it's off."
        report.mark()  # where everything stood, so the report card counts only this session
        self._launch()
        self._save()

    def _launch(self):
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def extend(self, n):
        n = max(1, min(200, int(n)))
        if not self.agents:
            raise ValueError("There's no session to continue. Start one first.")
        if core.budget_exceeded():
            raise ValueError("The budget is used up. Raise it in Settings > Budget first.")
        core.halt_clear()
        with self.cv:
            self.max_turns = max(self.max_turns, self.turn) + n
            self.cv.notify_all()
        if not self.alive:
            self.stop_flag, self.error, self.streak, self.final = False, "", 0, "idle"
            self._launch()
            self._save()

    def nudge(self):
        """Called right after you send a message, so the AIs actually answer it. Returns a short note for the UI.
        Running: the next turn reads it. Paused: one step (they answer once, then stay paused). Finished: +2 turns.
        Stopped / errored / emergency-stopped / awaiting recovery: left alone, because you ended those on purpose."""
        if self.recovery:
            return "Saved. This session is waiting on your Resume / Discard choice first."
        if self.alive and not self.paused:
            return "Sent. They'll see it on the next turn." if self.current else "Sent."
        if self.alive and self.paused:
            if core.halt_reason() or core.budget_exceeded():
                return "Sent, but the session is paused by a stop or the budget. Fix that, then Resume."
            self.step()  # one turn; stays paused afterwards
            return "Sent. The next AI will answer once (session stays paused)."
        if not self.agents:
            return "Saved to the chat. Start a session so they can answer."
        if self.final == "finished":
            try:
                self.extend(2)
            except ValueError as e:
                return f"Sent, but they can't continue: {e}"
            return "Sent. The session was finished, so I gave it 2 more turns."
        return "Saved to the chat, but the session is " + self.final + ". Start a new one or use +turns to continue."

    def set_models(self, claude=None, chatgpt=None):
        """Switch models live. Takes effect on that AI's next turn (a turn already in flight finishes on the old one)."""
        new = dict(self.models)
        if claude:
            new["claude"] = _valid_model(claude)
        if chatgpt:
            new["chatgpt"] = _valid_model(chatgpt)
        self.models = new
        for name, agent in self.agents.items():
            agent.model = new[name]

    def pause(self):
        """Soft pause: the AI mid-turn finishes that turn, then nothing new starts. Everything is kept."""
        with self.cv:
            self.paused = True
        self._save()

    def resume(self):
        if core.halt_reason():
            raise ValueError("Emergency stop is active. Use +turns or start a new session to lift it deliberately.")
        if core.budget_exceeded():
            raise ValueError("The budget is used up. Raise it in Settings > Budget first.")
        with self.cv:
            self.paused = False
            self.resume_at = None
            self.offline = {}
            for p in (getattr(self, "par", None) or {}).values():
                p["limit_until"] = None
                p.pop("offline", None)
            self.cv.notify_all()
        self.warning = ""
        self._save()

    def cancel_auto_resume(self):
        """Keep the session paused: don't pick up again on its own when the limit resets."""
        with self.cv:
            self.resume_at = None
            for p in (getattr(self, "par", None) or {}).values():
                if p.get("limit_until"):
                    p["limit_until"], p["idle"], p["mode"] = None, True, "waiting"
            self.cv.notify_all()
        self.warning = "Auto-resume cancelled. Press Resume (or send a message in Parallel mode) when you're ready."
        self._save()

    def limits(self):
        out = {}
        if self.resume_at:
            out["session"] = self.resume_at
        for n, p in (getattr(self, "par", None) or {}).items():
            if p.get("limit_until"):
                out[n] = p["limit_until"]
        return out

    def offline_seats(self):
        par = getattr(self, "par", None) or {}
        return sorted(set(self.offline) | {n for n, p in par.items() if p.get("offline")})

    def emergency_stop(self):
        """Hard stop: no new tool runs, no new model calls, pending approvals denied, session ends at the next
        step boundary. It can't undo something that already ran (the journal shows what did)."""
        core.halt_set("emergency stop pressed by you")
        self.stop()

    def step(self):
        with self.cv:
            self.paused = True
            self.step_n += 1
            for p in (getattr(self, "par", None) or {}).values():  # parallel: one turn for each AI
                p["steps"] += 1
            self.cv.notify_all()

    def busy(self):
        """Names of the AIs that are in the middle of a turn right now."""
        return list(self.currents or ([self.current] if self.current else []))

    def park(self, timeout=30):
        """Switching chats: end this session's loop quietly so it can be resumed exactly where it was. Only called when
        it's paused and no turn is in flight, so nothing gets cut off."""
        if not self.alive:
            return
        if self.busy():
            raise ValueError("an AI is still finishing its turn")
        with self.cv:
            self.parking, self.stop_flag = True, True
            self.cv.notify_all()
        self.thread.join(timeout)

    def stop(self):
        with self.cv:
            self.stop_flag, self.paused = True, False
            self.cv.notify_all()
        for r in core.pending_approvals():  # unblock a turn that is waiting on you: stopping counts as "no"
            core.decide_approval(r["id"], False)
        if not self.alive:  # a live loop thread writes "stopped" itself; saving "running" from here could land after it and fake a crash
            self._save("stopped")

    # ---- the turn loop ----------------------------------------------------
    def _loop(self):
        while True:
            if core.load_state().get("style") == "parallel" and not self.stop_flag:
                ended = self._parallel()
                if ended:
                    return
                continue  # style was switched away from parallel mid-session: carry on taking turns
            with self.cv:
                while self.paused and self.step_n == 0 and not self.stop_flag:
                    if self.resume_at and time.time() >= self.resume_at and self.offline:  # an AI dropped offline: is it back?
                        down = [n for n in self.offline if not self._reachable(n)]
                        if down:
                            self.offline[down[0]] += 1
                            self.resume_at = time.time() + self._offline_wait(self.offline[down[0]])
                            continue
                        back = ", ".join(core.PRETTY[n] for n in self.offline)
                        self.offline, self.resume_at, self.paused, self.warning = {}, None, False, ""
                        core.append_transcript("system", f"{back} is answering again, so the session carried on.")
                        notify.send("limit", f"{back} is back online, so the session is running again.", key="resumed")
                        break
                    if self.resume_at and time.time() >= self.resume_at:  # the usage limit should have reset: carry on
                        self.resume_at, self.paused, self.warning = None, False, ""
                        notify.send("limit", "The usage limit reset, so the session is running again.", key="resumed")
                        break
                    self.cv.wait(0.25)
                if self.stop_flag:
                    self._end("stopped")
                    return
                if core.load_state().get("style") == "parallel":
                    continue
                if self.paused:
                    self.step_n -= 1
                if self.turn >= self.max_turns:
                    self._end("finished", "turn_limit")
                    return
                name = self.order[self.turn % 2]
                self.current, self.currents = name, [name]
            kind, val = self._one(name)
            self.current, self.currents = None, []
            if kind == "interrupted":
                continue  # 'Send now': nothing counted, the next turn reads the message
            if self._handle_failure(name, kind, val):
                return
            if kind != "ok":  # budget / usage-limit pause: the cut-off turn doesn't count
                continue
            self._limit_tries.pop(name, None)
            self.turn += 1
            if self._check_stuck(name):
                with self.cv:
                    self.paused = True
                self._save("paused")
                continue
            self.streak = self.streak + 1 if "[DONE]" in val else 0
            if self.streak >= 2:
                self._end("finished", "completed")
                return
            self._save()

    def _one(self, name):
        """One turn. -> (kind, value): ok (reply text), interrupted, budget / halt (reason), limit, offline, error ('Type: message')."""
        j0 = max([e["id"] for e in core.load_state().get("journal", [])] or [0])
        try:
            return "ok", core.agent_turn(name, self.blender, self.agents[name])
        except core.Interrupted:
            return "interrupted", ""
        except core.Halted as h:
            return ("budget" if h.kind == "budget" else "halt"), h.reason
        except Exception as e:  # API errors, bad keys, etc: stop cleanly and tell the UI
            msg = f"{type(e).__name__}: {e}"
            is_limit, secs = core.usage_limit_info(msg)
            if is_limit:
                return "limit", (secs, msg)
            if _is_offline(e, msg):
                done = [x for x in core.load_state().get("journal", []) if x["id"] > j0 and x["by"] == name and x["status"] == "done"]
                return "offline", (msg, done)
            return "error", msg

    def _reachable(self, name):
        ag = self.agents.get(name)
        return providers.reachable(ag.profile) if isinstance(ag, providers.CompatAgent) else True  # a cloud AI: just try again

    @staticmethod
    def _offline_wait(tries):
        return min(300, 30 * (2 ** max(0, tries - 1)))  # 30s, 60s, 2 min, 4 min, then every 5 min

    def _offline_note(self, name, msg, done, auto):
        """What happened, said once in the chat: paused, nothing lost, what already ran (so it isn't done twice)."""
        ag = self.agents.get(name)
        where = f"{ag.profile['label']} at {ag.profile['base_url']}" if isinstance(ag, providers.CompatAgent) else "its server"
        warn = (f"{core.PRETTY[name]} lost its connection ({where} stopped answering). Paused: the conversation, board and files are "
                "kept, and the cut-off turn doesn't count. " + ("It picks back up by itself once it answers again (checked every 30s "
                "at first)." if auto else "Press Resume once it's back."))
        note = warn
        if done:
            note += ("\nBefore the connection dropped, these already ran and are NOT to be repeated (check the real state first):\n" +
                     "\n".join(f"- {x['tool']} {x['args']}" for x in done))
        return warn, note

    def _limit_wait(self, name, secs, msg):
        """A plan/rate limit: how long to wait before trying again. Unknown reset time = back off 15, 30, 60 min."""
        tries = self._limit_tries.get(name, 0)
        self._limit_tries[name] = tries + 1
        wait = secs if secs else min(3600, 900 * (2 ** tries))
        return time.time() + max(60, wait)

    def _check_stuck(self, name):
        """After a turn: did the stuck detector catch this AI going in circles? -> pause it and ping the human."""
        st = core.load_state().get("stuck", {}).get(name)
        if not st:
            return False
        core.mutate_state(lambda s_: (s_.setdefault("stuck", {}).pop(name, None), s_.setdefault("repeat", {}).pop(name, None)))
        self.warning = (f"{core.PRETTY[name]} looked stuck: the same {st['tool']} call failed {st['count']} times in a row. "
                        f"It's paused so it stops burning your usage. Send a message or press Resume when you're ready.")
        core.append_transcript("system", self.warning)
        notify.send("stuck", f"{core.PRETTY[name]} repeated the same failing {st['tool']} call {st['count']} times and was paused.",
                    preview=st.get("last", ""), key=f"stuck-{name}")
        return True

    def _handle_failure(self, name, kind, val):
        """Returns True if the session ended. A budget stop only pauses."""
        if kind == "ok":
            return False
        if kind == "budget":
            with self.cv:
                self.paused = True
            self.warning = f"Paused: {val}. Raise the budget in Settings > Budget, then Resume."
            self._save("paused")
            notify.send("limit", f"Paused: {val}.", key="budget")
            return False
        if kind == "limit":
            secs, msg = val
            with self.cv:
                self.paused = True
                if app_settings()["auto_resume"]:
                    self.resume_at = self._limit_wait(name, secs, msg)
            when = time.strftime("%I:%M %p", time.localtime(self.resume_at)).lstrip("0") if self.resume_at else None
            self.warning = (f"{core.PRETTY[name]} hit a usage limit. " + (f"Auto-resuming at {when} (nothing is lost; the cut-off turn "
                            "doesn't count)." if when else "Auto-resume is off: press Resume when the limit resets."))
            core.append_transcript("system", self.warning)
            self._save("paused")
            notify.send("limit", self.warning, key=f"limit-{name}")
            return False
        if kind == "offline":
            msg, done = val
            auto = app_settings()["auto_resume"]
            with self.cv:
                self.paused = True
                self.offline[name] = self.offline.get(name, 0) + 1
                self.resume_at = time.time() + self._offline_wait(self.offline[name]) if auto else None
            self.warning, note = self._offline_note(name, msg, done, auto)
            core.append_transcript("system", note)
            self._save("paused")
            notify.send("error", self.warning, key=f"offline-{name}")
            return False
        if kind == "halt":
            self.error = f"Stopped: {val}"
            self._end("stopped", "emergency")
            return True
        self.error = f"{core.PRETTY[name]} failed: {val}"
        self._end("error", "error", self.error)
        return True

    def _end(self, final, kind=None, note=""):
        if self.parking and final == "stopped":  # switched to another chat: no summary, resumable when you come back
            self.final = "parked"
            self._save("parked")
            return
        self.final = final
        self._save(final)
        try:
            if kind == "completed":
                notify.send("finished", f"Both AIs say they're done ({self.turn} turns).", key="end")
            elif final == "finished":
                notify.send("finished", f"The session reached its turn limit ({self.turn} turns).", key="end")
            elif final == "error":
                notify.send("error", note or self.error or "The session stopped with an error.", key="end")
            elif kind == "emergency":
                notify.send("error", "Emergency stop: nothing new will run.", key="end")
        except Exception:
            pass
        self._summarize(kind or ("emergency" if core.halt_reason() else final), note)
        self._report(kind or ("emergency" if core.halt_reason() else final), final, note)

    def _report(self, kind, final, note=""):
        """The report card: counted from recorded data, posted as a short note in the chat (full card in the app / Discord)."""
        try:
            try:
                chat = (chats.active() or {}).get("name", "") if chats.enabled() else ""
            except Exception:
                chat = ""
            rep = report.build(kind, final, {"turn": self.turn, "max_turns": self.max_turns, "models": self.models,
                                             "backends": self.backends, "chat": chat, "note": note or self.error})
            core.append_transcript("system", report.as_text(rep, short=True))
        except Exception as e:  # a report must never break ending a session
            self.warning = f"Couldn't make the report card: {type(e).__name__}: {e}"

    # ---- PARALLEL: each AI runs in its own loop, so neither waits for the other's turn --------------
    def _parallel(self):
        """Run both AIs side by side until the session ends (True) or the style is switched away (False).
        They still talk: each turn reads the latest messages from the other AI and the human.
        An AI that ends with [WAITING] or [DONE] sleeps until a new message from someone else arrives."""
        self.par = {n: {"idle": False, "done": False, "mode": None, "seen": len(core._read_json(core.TRANSCRIPT_PATH, [])), "steps": 0}
                    for n in self.order}
        self.par_end = None  # (final, kind, note) once something ends the session
        workers = [threading.Thread(target=self._worker, args=(n,), daemon=True) for n in self.order]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        self.current, self.currents = None, []
        if self.par_end:
            self._end(*self.par_end)
            return True
        return False

    def _news_for(self, name):
        """Should a sleeping AI wake up? Anything from the human does it. From the other AI: a [WAITING] AI wakes for
        anything except another [WAITING] (both waiting = both waiting on you), a [DONE] AI wakes for anything except
        another [DONE]. That stops two idle AIs from waking each other up forever."""
        msgs = core._read_json(core.TRANSCRIPT_PATH, [])
        mode = self.par[name]["mode"]
        for m in msgs[self.par[name]["seen"]:]:
            who, text = m.get("speaker"), m.get("text", "")
            if who in (name, "system"):
                continue
            if who == "human":
                if m.get("to") in core.PRETTY and m["to"] != name:
                    continue  # /claude or /gpt aimed at the other one
                return True
            if mode == "waiting" and "[WAITING]" not in text:
                return True
            if mode == "done" and "[DONE]" not in text:
                return True
        return False

    def _worker(self, name):
        me = self.par[name]
        while True:
            with self.cv:
                while not self.stop_flag and self.par_end is None:
                    if core.load_state().get("style") != "parallel":
                        return
                    if self.paused and me["steps"] == 0:
                        self.cv.wait(0.25)
                        continue
                    if me.get("limit_until"):
                        if time.time() < me["limit_until"]:
                            self.cv.wait(0.5)  # this AI is waiting for its usage limit to reset; the other keeps going
                            continue
                        if me.get("offline"):
                            if not self._reachable(name):
                                me["offline"] += 1
                                me["limit_until"] = time.time() + self._offline_wait(me["offline"])
                                continue
                            me.pop("offline")
                            me["limit_until"] = None
                            core.append_transcript("system", f"{core.PRETTY[name]} is answering again and is back to work.")
                            notify.send("limit", f"{core.PRETTY[name]} is back online.", key=f"resumed-{name}")
                        else:
                            me["limit_until"] = None
                            notify.send("limit", f"{core.PRETTY[name]}'s usage limit reset, so it's working again.", key=f"resumed-{name}")
                    if me["idle"] and not self._news_for(name):
                        self.cv.wait(0.5)  # waiting for a message: costs nothing
                        continue
                    break
                if self.par_end is not None:
                    return
                if self.stop_flag:
                    self.par_end = ("stopped", None, "")
                    self.cv.notify_all()
                    return
                if self.turn >= self.max_turns:
                    self.par_end = ("finished", "turn_limit", "")
                    self.cv.notify_all()
                    return
                if self.paused:
                    me["steps"] -= 1
                self.turn += 1  # reserve this turn now so the two loops can't overrun the limit together
                me["seen"] = len(core._read_json(core.TRANSCRIPT_PATH, []))
                self.currents = [n for n in self.order if n == name or n in self.currents]
                self.current = self.currents[0]
            kind, val = self._one(name)
            with self.cv:
                self.currents = [n for n in self.currents if n != name]
                self.current = self.currents[0] if self.currents else None
                if kind == "interrupted":
                    self.turn -= 1  # a cut-short turn doesn't count
                    me["idle"] = False
                    self.cv.notify_all()
                    continue
                if kind == "limit":
                    self.turn -= 1
                    secs, msg = val
                    if app_settings()["auto_resume"]:
                        me["limit_until"] = self._limit_wait(name, secs, msg)
                        when = time.strftime("%I:%M %p", time.localtime(me["limit_until"])).lstrip("0")
                        note = f"{core.PRETTY[name]} hit a usage limit and will pick up again at {when}. {core.PRETTY[core.OTHER[name]]} keeps working."
                    else:
                        me["idle"], me["mode"] = True, "waiting"
                        note = f"{core.PRETTY[name]} hit a usage limit. Auto-resume is off, so it waits until you send a message."
                    core.append_transcript("system", note)
                    notify.send("limit", note, key=f"limit-{name}")
                    self.cv.notify_all()
                    continue
                if kind == "offline":  # this AI waits for its server; the other one keeps working
                    self.turn -= 1
                    msg, done = val
                    auto = app_settings()["auto_resume"]
                    me["offline"] = me.get("offline", 0) + 1
                    if auto:
                        me["limit_until"] = time.time() + self._offline_wait(me["offline"])
                    else:
                        me["idle"], me["mode"] = True, "waiting"
                    _, note = self._offline_note(name, msg, done, auto)
                    core.append_transcript("system", note + f" {core.PRETTY[core.OTHER[name]]} keeps working.")
                    notify.send("error", note.split("\n")[0], key=f"offline-{name}")
                    self.cv.notify_all()
                    continue
                if kind != "ok":
                    self.turn -= 1
                    if kind == "budget":
                        self.paused = True
                        self.warning = f"Paused: {val}. Raise the budget in Settings > Budget, then Resume."
                    elif kind == "halt":
                        self.error = f"Stopped: {val}"
                        self.par_end = ("stopped", "emergency", "")
                    elif self.par_end is None:
                        self.error = f"{core.PRETTY[name]} failed: {val}"
                        self.par_end = ("error", "error", self.error)
                        self.stop_flag = True  # the other AI finishes its turn, then stops too
                    self.cv.notify_all()
                    if kind == "budget":
                        self._save("paused")
                        notify.send("limit", f"Paused: {val}.", key="budget")
                        continue
                    return
                self._limit_tries.pop(name, None)
                if self._check_stuck(name):  # paused until the human says something
                    me["done"], me["mode"], me["idle"] = False, "stuck", True
                    me["seen"] = len(core._read_json(core.TRANSCRIPT_PATH, []))
                    self.cv.notify_all()
                    continue
                if "[DONE]" in val:
                    me["done"], me["mode"] = True, "done"
                elif "[WAITING]" in val:
                    me["mode"] = "waiting"  # still counts as done if it was done before (woken, nothing left to do)
                else:
                    me["done"], me["mode"] = False, None  # did real work: not done any more
                me["idle"] = me["mode"] is not None
                me["seen"] = max(me["seen"], len(core._read_json(core.TRANSCRIPT_PATH, [])))
                if all(p["done"] for p in self.par.values()):
                    self.par_end = ("finished", "completed", "")
                elif all(p["mode"] == "waiting" for p in self.par.values()):
                    notify.send("waiting", "Both AIs are waiting on you.", preview=val, key="both-waiting")
                self.cv.notify_all()
            self._save()

    def direct_next(self, name):
        """/claude or /gpt: the named AI answers next (sequential styles); in Parallel only it wakes up."""
        with self.cv:
            if core.load_state().get("style") != "parallel" and self.order[self.turn % 2] != name:
                self.order.reverse()  # keeps alternating from there
            self.cv.notify_all()

    def set_max_turns(self, n):
        n = max(1, min(500, int(n)))
        with self.cv:
            self.max_turns = max(n, self.turn) if self.alive else n
            self.cv.notify_all()
        if self.alive:
            self._save()

    def send_now(self, to=None):
        """'Send now' button: cut the AIs' current turns short so the message you just sent is read immediately.
        Idle AIs wake up anyway, and a paused session answers once."""
        working = list(self.currents or ([self.current] if self.current else []))
        if to:
            working = [w for w in working if w in to]
        if working:
            core.interrupt(working)
        note = self.nudge()
        return (f"Sent now. Interrupted {' and '.join(core.PRETTY[n] for n in working)} so they read it right away."
                if working else note)

    def _summarize(self, kind, note=""):
        """File an end-of-session summary under the session's memory project. Built from recorded data (never from an AI), and
        only when the session ended in a way the app saw. Memory trouble must never affect the session itself."""
        try:
            pid = self.memory_project or memory.active_project()
            if pid:
                memory.write_summary(pid, kind, self.turn, self.max_turns, note)
        except Exception:
            pass

    # ---- subscription (CLI) backends -----------------------------------------
    def backend_info(self):
        """Which official CLIs are installed and signed in, so the UI can offer Subscription mode honestly."""
        if self.demo:
            return {n: {"found": True, "logged_in": True, "detail": "demo mode"} for n in ("claude", "chatgpt")}
        out = {}
        for n in ("claude", "chatgpt"):
            found, ok, detail = cli_agents.login_status(n)
            out[n] = {"found": found, "logged_in": ok, "detail": detail}
        return out

    def backend_test(self, agent, model=None):
        """One tiny real turn through the CLI + tool server, so setup problems show up with the real error text."""
        if agent not in core.OTHER:
            raise ValueError("agent must be claude or chatgpt")
        if self.alive:
            raise ValueError("A session is running. Stop it first, then test.")
        if self.demo:
            return {"ok": True, "reply": "BRIDGE OK (demo)", "seconds": 0.0}
        found, ok, detail = cli_agents.login_status(agent)
        if not found or not ok:
            return {"ok": False, "error": detail, "seconds": 0.0}
        ag = cli_agents.make_cli_agent(agent, _valid_model(model) if model else self.models[agent])
        ag.timeout = 180
        t0 = time.time()
        # A random code goes in a file only the guarded read_file tool can show it. Echoing the code back proves the tool
        # call really ran (a model that can't reach its tools can't guess it), not just that the CLI answered.
        nonce = secrets.token_hex(4)
        probe = core.WORKSPACE / "bridge_connection_test.txt"
        probe.write_text(f"connection test code: {nonce}\n", encoding="utf-8")
        try:
            reply = ag.turn("You are a connection test for AI Bridge. Be brief.",
                            "Call the read_file tool with path bridge_connection_test.txt. Then reply with exactly "
                            "'BRIDGE OK ' followed by the 8-character code from that file.")
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "seconds": round(time.time() - t0, 1)}
        finally:
            probe.unlink(missing_ok=True)
        good = f"BRIDGE OK {nonce}" in reply
        return {"ok": good, "reply": reply[:300], "seconds": round(time.time() - t0, 1),
                "note": "" if good else "It answered, but it never read the test file, so its tool calls aren't getting through."}

    # ---- persistence + crash recovery -------------------------------------
    def _save(self, status=None):
        """Write what's needed to pick this session back up. Conversation, board, decisions and approval history
        already live in transcript.json / state.json; this adds the coordinator's own position."""
        with self._save_lock:
            try:
                core._write_json(core.SESSION_PATH, {
                    "saved": core._now(), "status": status or self.status(), "turn": self.turn,
                    "max_turns": self.max_turns, "streak": self.streak, "first": self.first, "order": self.order,
                    "blender": self.blender, "models": self.models, "mode": core.MODE, "demo": self.demo,
                    "backends": self.backends, "providers": self.providers, "names": self.names, "memory_project": self.memory_project})
            except OSError:
                pass  # saving is best-effort, it must never take the session down
        try:
            chats.touch()
        except Exception:
            pass
        try:  # optional "still going" check-ins every N minutes while a session runs
            ns = notify.get()
            if self.alive and not self.paused and ns["events"].get("progress"):
                last = getattr(self, "_progress_at", 0) or time.time()
                self._progress_at = last
                if time.time() - last >= ns["progress_minutes"] * 60:
                    self._progress_at = time.time()
                    board = core.load_state().get("board", [])
                    done = sum(1 for t in board if t["status"] == "done")
                    notify.send("progress", f"Still going: turn {self.turn}/{self.max_turns}, {done}/{len(board)} board tasks done.", key=f"p{self.turn}")
        except Exception:
            pass

    def recover(self):
        """Called once at app start. If the last run was still going (running/paused) the app died or was closed
        mid-session: stale approval requests are denied, in-flight tool calls are marked interrupted (never re-run),
        and the UI offers to resume. Nothing restarts by itself."""
        try:
            fr = core.recover_files()  # settle any file write that was cut off (whole old file or whole new file, never half)
        except Exception as e:
            fr = {"writes": [], "temp_files": [], "error": str(e)}
        self.file_recovery = fr if (fr["writes"] or fr["temp_files"]) else None
        if fr["writes"]:
            core.append_transcript("system", "The app was stopped while saving files. " + "; ".join(
                f"{w['path']}: {w['note']}" for w in fr["writes"]) + ". Each one can be undone from Files > Recent changes.")
        d = core._read_json(core.SESSION_PATH, None)
        if isinstance(d, dict) and d.get("status") == "parked":  # you switched away from this chat while it was paused
            self.recovery = {"saved": d.get("saved", ""), "turn": d.get("turn", 0), "max_turns": d.get("max_turns", 0),
                             "models": d.get("models") or {}, "mode": d.get("mode", "approval"), "interrupted": [],
                             "expired_approvals": 0, "demo": bool(d.get("demo")), "parked": True}
            return
        if not isinstance(d, dict) or d.get("status") not in ("running", "paused"):
            return
        stale = core.pending_approvals()
        for r in stale:
            core.decide_approval(r["id"], False)
        interrupted = core.recover_journal()
        try:  # say plainly that the session was cut off; never write a completion summary for it
            pid = d.get("memory_project") or ""
            if pid and (memory.root() / pid / "meta.json").exists():
                memory.write_interrupted_marker(pid, {"saved": d.get("saved", ""), "turn": d.get("turn", 0), "max_turns": d.get("max_turns", 0),
                                                      "task": core.load_state().get("task", ""), "interrupted": interrupted},
                                                key=d.get("saved", ""))
        except Exception:
            pass
        self.recovery = {"saved": d.get("saved", ""), "turn": d.get("turn", 0), "max_turns": d.get("max_turns", 0),
                         "models": d.get("models") or {}, "mode": d.get("mode", "approval"),
                         "interrupted": interrupted, "expired_approvals": len(stale), "demo": bool(d.get("demo"))}

    def recover_action(self, action):
        d = core._read_json(core.SESSION_PATH, None)
        if self.recovery is None or not isinstance(d, dict):
            raise ValueError("There's nothing to recover.")
        if self.alive:
            raise ValueError("A session is already running.")
        if action == "discard":
            d["status"] = "discarded"
            core._write_json(core.SESSION_PATH, d)
            self.recovery = None
            return
        if action != "resume":
            raise ValueError("action must be resume or discard")
        models = d.get("models") or {}
        cm = _valid_model(models.get("claude") or self.models["claude"])
        gm = _valid_model(models.get("chatgpt") or self.models["chatgpt"])
        backends = {k: (d.get("backends") or {}).get(k, "api") for k in ("claude", "chatgpt")}
        provs = {k: (d.get("providers") or {}).get(k, {"claude": "claude", "chatgpt": "openai"}[k]) for k in ("claude", "chatgpt")}
        agents = self._make_agents(cm, gm, backends, provs)
        self.backends, self.providers = backends, provs
        if d.get("names"):
            self.names = core.set_seat_names(d["names"])
        parked = bool(self.recovery.get("parked"))
        if parked and d.get("mode") in ("approval", "readonly", "autonomous"):
            mode = d["mode"]  # you paused it yourself to switch chats, so it comes back exactly as you left it
        else:
            mode = d.get("mode") if d.get("mode") in ("approval", "readonly") else "approval"  # never come back hands-off
        core.set_mode(mode)
        for n, b in backends.items():
            core.set_billing(n, self._billing(b))
        core.set_approver(None)
        core.halt_clear()
        if self.recovery["interrupted"]:
            core.append_transcript("system", core.interrupted_note(self.recovery["interrupted"]))
        self.models = {"claude": cm, "chatgpt": gm}
        self.agents, self.blender = agents, bool(d.get("blender")) and core.blender_alive()
        self.first = d.get("first") if d.get("first") in core.OTHER else "claude"
        self.order = d.get("order") if d.get("order") in (["claude", "chatgpt"], ["chatgpt", "claude"]) else [self.first, core.OTHER[self.first]]
        self.turn, self.max_turns, self.streak = int(d.get("turn", 0)), int(d.get("max_turns", 12)), int(d.get("streak", 0))
        self.stop_flag, self.paused, self.step_n, self.final, self.error = False, True, 0, "idle", ""
        self.warning = ("Welcome back. This chat's session is paused where you left it; press Resume to continue." if parked else
                        "Recovered. Safety mode is " + mode + (" (hands-off is never restored automatically)" if d.get("mode") == "autonomous" else "")
                        + ". It's paused so you can look around; press Resume to continue.")
        self.recovery = None
        self._launch()
        self._save()

    # ---- what the UI sees -------------------------------------------------
    def _memory_badge(self):
        try:
            pid = memory.active_project()
            if not pid:
                return {"active": "", "name": "", "pending": 0, "mode": ""}
            return {"active": pid, "name": memory._meta(pid).get("name", pid), "pending": len(memory.list_pending(pid)),
                    "mode": memory.effective_mode(pid)}
        except Exception:
            return {"active": "", "name": "", "pending": 0, "mode": ""}

    def snapshot(self):
        s = core.load_state()
        if self.warning.startswith("Paused: budget") and not core.budget_exceeded():
            self.warning = ""
        all_msgs = core._read_json(core.TRANSCRIPT_PATH, [])
        tail = all_msgs[-300:]
        lock = core.read_writer()
        return {
            "session": {"status": self.status(), "turn": self.turn, "max_turns": self.max_turns,
                        "current": self.current, "currents": list(self.currents or ([self.current] if self.current else [])), "error": core.redact(self.error), "warning": core.redact(self.warning), "limits": self.limits(), "offline": self.offline_seats(),
                        "mode": core.MODE, "style": s["style"], "lead": s["lead"], "blender": self.blender,
                        "first": self.first, "demo": self.demo, "models": self.models, "backends": self.backends,
                        "providers": self.providers, "names": dict(core.PRETTY)},
            "presets": {"claude": CLAUDE_PRESETS, "chatgpt": GPT_PRESETS},
            "ai_profiles": providers.profiles(),
            "report": report.latest(),
            "ai_presets": {k: {x: v[x] for x in ("label", "base_url", "needs_key", "local", "help")} for k, v in providers.PRESETS.items()},
            "keys": {"anthropic": bool(os.getenv("ANTHROPIC_API_KEY")), "openai": bool(os.getenv("OPENAI_API_KEY"))},
            "transcript": {"total": len(all_msgs), "messages": tail},
            "state": {"task": s["task"], "board": s["board"], "decisions": s["decisions"][-50:],
                      "notes": s["notes"][-30:], "changes": s["changes"][-30:], "approvals": s["approvals"][-30:],
                      "versions": s["versions"][-10:], "flags": [f for f in s["flags"] if not f["resolved"]],
                      "disagreements": [{k: v for k, v in d.items() if k != "key"} for d in s.get("disagreements", [])[-20:]],
                      "writer": lock["owner"] if lock else None},
            "pending": core.pending_approvals(),
            "usage": core.usage_summary(),
            "perms": {"values": core.get_perms(), "groups": core.perm_groups()},
            "activity": core.activity_snapshot(),
            "journal": s["journal"][-15:],
            "halted": core.halt_reason(),
            "recovery": self.recovery,
            "recent_changes": core.file_versions(limit=8),
            "file_recovery": self.file_recovery,
            "skills_on": _skills_on(),
            "memory": self._memory_badge(),
            "chat": chats.active() if chats.enabled() else None,
            "notices": notify.take_desktop(),
        }


def _env_write(updates):
    """Set (value) or remove (None) lines in .env, and in the live environment. Written atomically."""
    for k, v in updates.items():
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k) or (v is not None and any(ch in str(v) for ch in "\r\n")):
            raise ValueError("that value can't be saved (line breaks aren't allowed)")
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    out, seen = [], set()
    for ln in lines:
        k = ln.split("=", 1)[0].strip()
        if k in updates:
            seen.add(k)
            if updates[k] is not None:
                out.append(f"{k}={updates[k]}")
        else:
            out.append(ln)
    out += [f"{k}={v}" for k, v in updates.items() if k not in seen and v is not None]
    tmp = ENV_PATH.with_suffix(".env.tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, ENV_PATH)
    for k, v in updates.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    core._REDACT_CACHE["at"] = 0  # a key was just saved or removed: look again right away


def _skills_on():
    try:
        have = {k["id"]: k["name"] for k in skills.listing()}
        return {a: [{"id": x, "name": have.get(x, x)} for x in lst] for a, lst in skills.active().items()}
    except Exception:
        return {"claude": [], "chatgpt": []}


APP_DEFAULTS = {"auto_resume": True, "close_action": "ask", "image_gen": "ask", "imagegen_warned": False,
                "blender": False, "blender_host": "", "blender_port": 0}  # Blender lives in Settings > Connectors ("" / 0 = .env or the default)


def app_settings():
    s = dict(APP_DEFAULTS)
    s.update({k: v for k, v in (core._read_json(core.DATA / "app_settings.json", {}) or {}).items() if k in APP_DEFAULTS})
    return s


def app_settings_save(b):
    s = app_settings()
    if "auto_resume" in b:
        s["auto_resume"] = bool(b["auto_resume"])
    if "image_gen" in b:
        if b["image_gen"] not in cli_agents.IMAGE_MODES:
            raise ValueError("image_gen must be ask, allowed or off")
        s["image_gen"] = b["image_gen"]
    if "blender" in b:
        s["blender"] = bool(b["blender"])
    if "blender_host" in b:
        hst = str(b["blender_host"]).strip()
        if not re.match(r"^[A-Za-z0-9.\-]{1,253}$|^\[?::1\]?$", hst):
            raise ValueError("the Blender host should be like localhost or 127.0.0.1")
        s["blender_host"] = hst
    if "blender_port" in b:
        try:
            port = int(b["blender_port"])
        except (TypeError, ValueError):
            raise ValueError("the Blender port is a number (the add-on's default is 9876)")
        if not 1 <= port <= 65535:
            raise ValueError("the Blender port is a number from 1 to 65535")
        s["blender_port"] = port
    if "close_action" in b:
        if b["close_action"] not in ("ask", "tray", "quit"):
            raise ValueError("close_action must be ask, tray or quit")
        s["close_action"] = b["close_action"]
    core._write_json(core.DATA / "app_settings.json", s)
    apply_blender_settings()
    return s


def apply_blender_settings():
    """Where the Blender add-on listens. Also put in the environment, so the tool server the plan-mode CLIs start uses it too."""
    s = app_settings()
    host, port = s["blender_host"] or core.BLENDER_HOST, int(s["blender_port"] or core.BLENDER_PORT)
    if (core.BLENDER_HOST, core.BLENDER_PORT) != (host, port):
        core._proto = None  # a different Blender: detect its add-on again
    core.BLENDER_HOST, core.BLENDER_PORT = host, port
    if s["blender_host"] or s["blender_port"]:
        os.environ["BLENDER_HOST"], os.environ["BLENDER_PORT"] = host, str(port)


def blender_test():
    try:
        alive = core.blender_alive()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not alive:
        return {"ok": False, "error": f"Nothing is answering on {core.BLENDER_HOST}:{core.BLENDER_PORT}. In Blender, turn on the MCP add-on's server "
                                       "(Edit > Preferences > Add-ons > MCP, or the BlenderMCP panel > Connect)."}
    return {"ok": True, "protocol": core.blender_protocol() or "", "where": f"{core.BLENDER_HOST}:{core.BLENDER_PORT}"}


def notify_info():
    return {"settings": notify.get(), "events": notify.EVENTS, "log": notify.SENT_LOG[-10:]}


def notify_save(b):
    notify.save(b)
    return notify_info()


def discord_post(app, b):
    """Settings > Discord. The token goes to .env and is never sent back."""
    svc, a = app.discord, b.get("action")
    if a == "token":
        tok = str(b.get("token", "")).strip()
        if not discord_bot.token_ok(tok):
            raise ValueError("That doesn't look like a bot token. Developer Portal > your app > Bot > Reset Token, then Copy.")
        _env_write({discord_bot.TOKEN_ENV: tok})
        svc.restart()
    elif a == "forget_token":
        svc.stop()
        _env_write({discord_bot.TOKEN_ENV: None})
        svc.state, svc.detail, svc.bot_name, svc.app_id = "off", "", "", ""
    elif a == "reconnect":
        svc.restart()
    elif a == "link":
        if svc.state != "online":
            raise ValueError("The bot isn't online yet. Save its token first and wait for it to connect.")
        svc.bridge.new_link_code()
    elif a == "cancel_link":
        svc.bridge.link = None
    elif a == "unlink":
        svc.bridge.unlink()
    elif a == "settings":
        c = discord_bot.config()
        for k in ("mirror", "previews"):
            if k in b:
                c[k] = bool(b[k])
        if "enabled" in b:
            c["enabled"] = bool(b["enabled"])
        discord_bot.save_config(c)
        if "enabled" in b:
            svc.start() if c["enabled"] else svc.stop()
    elif a == "test":
        if not svc.bridge.linked():
            raise ValueError("Link a channel first.")
        to = notify.send("test", "Test from AI Bridge. If you can read this here, notifications work.", force=True)
        return {"ok": True, "to": to, **svc.info()}
    else:
        raise ValueError("unknown action")
    return {"ok": True, **svc.info()}


def connectors_info():
    """Connector settings for the UI. Secrets are never sent back, only whether one is saved."""
    import connectors
    out = {}
    defs = connectors.defs()
    for name, c in connectors.load_config().items():
        p = defs[name]
        out[name] = {"label": p["label"], "help": p["help"], "enabled": c["enabled"], "transport": c["transport"],
                     "command": c["command"], "args": c["args"], "url": c["url"], "needs_key": bool(c["key_env"]),
                     "has_key": connectors.has_key(name), "custom": p["custom"], "key_header": c["key_header"],
                     "env": [{"name": v, "filled": bool(os.getenv(connectors._secret_name(name, v)))} for v in c["env_names"]],
                     "read_tools": c["read_tools"] or [], "missing": connectors.missing_secrets(name),
                     "env_optional": bool(p.get("env_optional")), "key_label": p.get("key_label", ""), "blurb": p.get("blurb", "")}
    return out


def connectors_save(b):
    import connectors
    name = str(b.get("name", ""))
    if name not in connectors.defs():
        raise ValueError("unknown connector")
    key = (b.get("key") or "").strip()
    if key:
        env = connectors.load_config()[name]["key_env"]
        if not env:
            raise ValueError("this connector doesn't use a key")
        if len(key) > 2000 or any(ch in key for ch in "\r\n"):
            raise ValueError("That doesn't look like a valid API key.")
        _env_write({env: key})
    env_vals = b.get("env") or {}
    if env_vals:  # fill in a variable of a connector you added (blank = leave as is)
        allowed = set(connectors.load_config()[name]["env_names"])
        _env_write({connectors._secret_name(name, k): str(v) for k, v in env_vals.items() if k in allowed and str(v).strip()})
    connectors.save_config(name, enabled=b.get("enabled"), command=b.get("command"), args=b.get("args"), url=b.get("url"),
                           read_tools=b.get("read_tools"))
    return connectors_info()


def connectors_paste(b):
    """Preview a pasted README config. Secret values are never sent back: only which ones are filled in."""
    import connectors
    return {"servers": [connectors.preview(s_) for s_ in connectors.parse_paste(b.get("text", ""))]}


def connectors_add(b):
    """Add a connector: from a pasted config (text + index + fill-ins) or from the form (spec). Starts switched off."""
    import connectors
    if "text" in b:
        specs = connectors.parse_paste(b["text"])
        i = int(b.get("index", 0))
        if not 0 <= i < len(specs):
            raise ValueError("pick one of the servers in the pasted config")
        spec = specs[i]
        fills = b.get("fill") or {}
        if b.get("label"):
            spec["label"] = b["label"]
        for var, val in (fills.get("env") or {}).items():
            if var in spec["env"] and str(val).strip():
                spec["env"][var] = str(val).strip()
        if str(fills.get("key") or "").strip():
            spec["key"] = str(fills["key"]).strip()
    else:
        spec = b.get("spec") or {}
        if isinstance(spec.get("env"), str):  # NAME=value lines from the form
            env = {}
            for ln in spec["env"].splitlines():
                if ln.strip():
                    if "=" not in ln:
                        raise ValueError(f"each variable line needs NAME=value ({ln.strip()[:30]!r})")
                    k, v = ln.split("=", 1)
                    env[k.strip()] = v.strip()
            spec["env"] = env
    cid = b.get("id") or None
    if cid:  # editing: keep saved secrets unless new ones are typed
        old = connectors.load_config().get(cid)
        if not old:
            raise ValueError("that connector doesn't exist")
        if not str(spec.get("key") or "").strip() and old["key_env"] and os.getenv(old["key_env"]):
            spec["keep_key"] = True
        env = dict(spec.get("env") or {})
        for v in old["env_names"]:
            if v in env and not str(env[v] or "").strip():
                env[v] = None  # blank = keep what's saved
        spec["env"] = env
    cid, secrets = connectors.add_custom(spec, cid)
    _env_write(secrets)
    return {"id": cid, "connectors": connectors_info()}


def connectors_remove(b):
    import connectors
    _env_write(connectors.remove_custom(str(b.get("id", ""))))
    return connectors_info()


def save_keys(anthropic="", openai=""):
    """Write keys into .env (plain text, next to the app, like the manual setup) and the live environment."""
    updates = {}
    for env, val in (("ANTHROPIC_API_KEY", anthropic), ("OPENAI_API_KEY", openai)):
        val = (val or "").strip()
        if val:
            if len(val) > 400 or any(c in val for c in " \r\n\t\"'="):
                raise ValueError(f"That doesn't look like a valid value for {env}.")
            updates[env] = val
    if not updates:
        raise ValueError("Paste at least one key.")
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    out, seen = [], set()
    for ln in lines:
        k = ln.split("=", 1)[0].strip()
        if k in updates:
            out.append(f"{k}={updates[k]}")
            seen.add(k)
        else:
            out.append(ln)
    out += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ.update(updates)


def open_folder(path):
    try:
        if hasattr(os, "startfile"):
            os.startfile(str(path))  # Windows
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        raise ValueError(f"Couldn't open the folder: {e}")


# --------------------------------------------------------------------------- local web server
class App:
    def __init__(self, demo=False, port=8750, demo_delay=1.2, use_chats=False):
        self.demo, self.demo_delay = demo, demo_delay
        if use_chats:
            chats.init()  # migrates an old single-chat install on first run, then opens the last chat
        self.session = Session(demo, demo_delay)
        self.session.recover()
        self.discord = discord_bot.DiscordService(self)  # started by main() (not by tests)
        apply_blender_settings()
        self.token = secrets.token_urlsafe(24)
        self.httpd = None
        handler = self._make_handler()
        for p in ([port] if port == 0 else range(port, port + 20)):
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", p), handler)
                break
            except OSError:
                continue
        if self.httpd is None:
            raise OSError(f"couldn't open a local port starting at {port}")
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/"

    def switch_chat(self, cid, pause=False):
        """Open another chat. A running session here is paused first (only if you said so), and parked once the AI that
        is mid-turn finishes, so it resumes exactly where it was when you come back."""
        if not chats.enabled():
            raise ValueError("chats aren't set up")
        s = self.session
        if cid == (chats.active() or {}).get("id"):
            return {"ok": True}
        if s.alive and not s.parking:
            if not pause:
                return {"ok": False, "needs_pause": True, "busy": s.busy(),
                        "note": "A session is running in this chat. Pause it to switch? It'll stay paused here until you come back."}
            if not s.paused:
                s.pause()
            deadline = time.time() + 3
            while s.busy() and time.time() < deadline:
                time.sleep(0.1)
            if s.busy():
                return {"ok": False, "waiting": s.busy(),
                        "note": f"Paused. Waiting for {' and '.join(core.PRETTY[n] for n in s.busy())} to finish their current turn..."}
            s.park()
        chats.switch(cid)
        self.session = Session(self.demo, self.demo_delay)
        self.session.recover()
        return {"ok": True}

    # ---- skills (Settings > Skills) ----
    def stage_skill(self, files, source):
        """Keep an uploaded skill in memory until you confirm it (10 minutes)."""
        now = time.time()
        self._staged = {k: v for k, v in getattr(self, "_staged", {}).items() if now - v["t"] < 600}
        tok = secrets.token_hex(8)
        self._staged[tok] = {"files": files, "source": source, "t": now}
        return {"token": tok, "preview": skills.preview(files, source)}

    def skills_post(self, act, b):
        if act == "scan":
            return {"found": skills.scan()}
        if act == "preview_found":
            return {"preview": skills.preview_found(b.get("path", ""))}
        if act == "import_found":
            done = [skills.import_found(p, replace=bool(b.get("replace"))) for p in (b.get("paths") or [])[:100]]
            return {"added": done, "skills": skills.listing()}
        if act == "folder":  # files picked with the folder picker -> preview
            return self.stage_skill(skills.files_from_upload(b.get("files") or []), b.get("source", "uploaded folder"))
        if act == "confirm":
            st = getattr(self, "_staged", {}).pop(str(b.get("token", "")), None)
            if not st:
                raise ValueError("that upload expired; pick the file again")
            sid = skills.add(st["files"], st["source"], replace=bool(b.get("replace")))
            return {"added": sid, "skills": skills.listing()}
        if act == "write":
            sid = skills.write(b.get("name"), b.get("description"), b.get("body"), b.get("id") or None)
            return {"added": sid, "skills": skills.listing()}
        if act == "remove":
            skills.remove(str(b.get("id", "")))
        elif act == "auto":
            skills.set_auto(str(b.get("id", "")), bool(b.get("on")))
        elif act == "on":
            skills.turn_on(str(b.get("id", "")), tuple(a for a in (b.get("who") or skills.AIS) if a in skills.AIS))
        elif act == "off":
            skills.turn_off(b.get("id") or None, tuple(a for a in (b.get("who") or skills.AIS) if a in skills.AIS))
        else:
            raise ValueError("unknown skills action")
        return {"skills": skills.listing(), "active": skills.active()}

    def serve_in_thread(self):
        t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        t.start()
        return t

    def _make_handler(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "AIBridge"

            def log_message(self, *a):
                pass

            # ---- plumbing
            def _send(self, code, body, ctype="application/json; charset=utf-8"):
                data = body if isinstance(body, bytes) else body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(data)

            def _json(self, obj, code=200):
                self._send(code, json.dumps(obj))

            def _guard(self, api):
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
                if host not in ("127.0.0.1", "localhost"):
                    self._json({"error": "bad host"}, 403)
                    return False
                origin = self.headers.get("Origin")
                if origin and origin not in (f"http://127.0.0.1:{app.port}", f"http://localhost:{app.port}"):
                    self._json({"error": "bad origin"}, 403)
                    return False
                if api and self.headers.get("X-Bridge-Token") != app.token:
                    self._json({"error": "missing or wrong token"}, 401)
                    return False
                return True

            def _raw_body(self, limit):
                n = int(self.headers.get("Content-Length") or 0)
                if n > limit:  # read and drop it (up to a point) so the browser gets our message instead of a dropped connection
                    left = min(n, 64_000_000)
                    while left > 0:
                        chunk = self.rfile.read(min(65536, left))
                        if not chunk:
                            break
                        left -= len(chunk)
                    raise ValueError(f"that file is {n // 1000} KB; the limit is {limit // 1000} KB")
                return self.rfile.read(n) if n else b""

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_BODY:
                    raise ValueError("request too large")
                raw = self.rfile.read(n) if n else b"{}"
                return json.loads(raw.decode("utf-8") or "{}")

            # ---- project memory (the human's side: edit, approve, roll back, configure)
            def _memory_get(self, u):
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                pid = q.get("project") or memory.active_project()
                if u.path == "/api/memory":
                    return self._json(memory.overview(q.get("project")))
                if not pid:
                    raise ValueError("no project is active")
                if u.path == "/api/memory/file":
                    text, rev = memory.read_file(pid, q.get("file", ""))
                    return self._json({"file": q.get("file"), "revision": rev, "text": text})
                if u.path == "/api/memory/search":
                    return self._json({"hits": memory.search(pid, q.get("q", ""), q.get("file", ""), 20)})
                if u.path == "/api/memory/history":
                    return self._json({"history": memory.history(pid, q.get("file") or None, 40)})
                if u.path == "/api/memory/diff":
                    f, a_, b_ = q.get("file", ""), int(q.get("a", 0)), q.get("b", "")
                    old = memory.version_text(pid, f, a_)
                    new = memory.read_file(pid, f)[0] if b_ in ("", "current") else memory.version_text(pid, f, int(b_))
                    return self._json({"diff": memory.diff_text(old, new, f"r{a_}", f"r{b_ or 'current'}")})
                if u.path == "/api/memory/summary":
                    return self._json({"text": memory.read_summary(pid, q.get("name", ""))})
                if u.path == "/api/memory/context":
                    return self._json({"text": memory.context_text(pid)})
                return self._json({"error": "not found"}, 404)

            def _chats_post(self, path, b):
                if not chats.enabled():
                    raise ValueError("chats aren't set up")
                kind, act = path.split("/")[2], path.split("/")[3]
                cid, pid = str(b.get("id", "")), str(b.get("id", ""))
                if kind == "chats":
                    if act == "new":
                        picked = [str(x) for x in (b.get("skills") or [])][:50]
                        bad = [x for x in picked if x not in {k["id"] for k in skills.listing()}]
                        if bad:  # checked before anything is created
                            raise ValueError("unknown skill: " + ", ".join(bad))
                        new = chats.create_chat(b.get("name", ""), str(b.get("project") or ""), b.get("access_add") or [], b.get("access_remove") or [])
                        skills.preset_chat(chats.chat_dir(new), picked)
                        res = {"ok": True, "id": new}
                        if b.get("open", True):
                            res.update(app.switch_chat(new, pause=bool(b.get("pause"))), id=new)
                        return res
                    if act == "switch":
                        return app.switch_chat(cid, pause=bool(b.get("pause")))
                    if act == "rename":
                        chats.rename_chat(cid, b.get("name", ""))
                    elif act == "move":
                        chats.move_chat(cid, str(b.get("project") or ""))
                    elif act == "access":
                        chats.set_access(cid, b.get("add"), b.get("remove"))
                    elif act == "delete":
                        chats.delete_chat(cid)
                    else:
                        raise ValueError("unknown chat action")
                    return {"ok": True}
                if act == "new":
                    return {"ok": True, "id": chats.create_project(b.get("name", ""))}
                if act == "rename":
                    chats.rename_project(pid, b.get("name", ""))
                elif act == "delete":
                    chats.delete_project(pid, bool(b.get("delete_chats")))
                else:
                    raise ValueError("unknown project action")
                return {"ok": True}

            def _memory_post(self, path, b):
                act = path.rsplit("/", 1)[1]
                pid = b.get("project") or memory.active_project()
                if act == "project":  # create / select
                    if b.get("action") == "create":
                        new = memory.create_project(b.get("name", ""), b.get("goals", ""))
                        memory.set_settings(active=new)
                        return {"ok": True, "project": new}
                    if b.get("action") == "select":
                        memory.set_settings(active=str(b.get("project") or ""))
                        return {"ok": True}
                    raise ValueError("action must be create or select")
                if act == "settings":
                    if b.get("project_mode") is not None:
                        memory.set_project_mode(pid, b["project_mode"])
                    memory.set_settings(mode=b.get("mode"), autoload=b.get("autoload"), context_chars=b.get("context_chars"))
                    return {"ok": True}
                if not pid:
                    raise ValueError("no project is active")
                if act == "save":  # the human edits a file by hand
                    return {"ok": True, **memory.human_save(pid, b.get("file", ""), str(b.get("text", "")), b.get("base_revision"))}
                if act == "pending":
                    return {"ok": True, **memory.decide_pending(pid, b.get("id"), bool(b.get("approve")), str(b.get("reason", "")))}
                if act == "restore":
                    return {"ok": True, **memory.human_restore(pid, b.get("file", ""), b.get("revision"))}
                if act == "ack":
                    memory.dismiss_tamper(pid, b.get("file", ""))
                    return {"ok": True}
                raise ValueError("unknown memory action")

            # ---- routes
            def do_GET(self):
                u = urlparse(self.path)
                if not self._guard(u.path.startswith("/api/")):
                    return
                try:
                    if u.path == "/":
                        html = (UI_DIR / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", app.token)
                        return self._send(200, html, "text/html; charset=utf-8")
                    if u.path == "/favicon.ico":
                        return self._send(204, b"", "image/x-icon")
                    if u.path == "/api/state":
                        return self._json(app.session.snapshot())
                    if u.path.startswith("/api/memory"):
                        return self._memory_get(u)
                    if u.path == "/api/image":
                        q = {k: v[0] for k, v in parse_qs(u.query).items()}
                        m, data = images.read(q.get("id", ""))
                        return self._send(200, data, m["mime"])
                    if u.path == "/api/files":
                        rows = [{"path": str(p.relative_to(core.WORKSPACE)).replace("\\", "/"), "size": p.stat().st_size}
                                for p in sorted(core.WORKSPACE.rglob("*")) if p.is_file()
                                and p.suffix not in (".lock", ".tmp") and "backups" not in p.relative_to(core.WORKSPACE).parts[:1]]
                        return self._json({"files": rows[:300], "folder": str(core.WORKSPACE), "changes": core.file_versions(limit=40)})
                    if u.path == "/api/file":
                        rel = (parse_qs(u.query).get("path") or [""])[0]
                        p = core._safe_path(rel)
                        if not p.is_file():
                            raise ValueError("no such file")
                        return self._json({"path": rel, "text": core._trunc(p.read_text(encoding="utf-8", errors="replace"), 200_000)})
                    if u.path == "/api/models":
                        return self._json(list_models(app.session.demo))
                    if u.path == "/api/ai/models":
                        pid = (parse_qs(u.query).get("id") or [""])[0]
                        if app.session.demo:
                            return self._json({"models": ["llama3.1:8b", "qwen2.5:14b", "mistral-nemo:12b", "gemma3:4b"], "demo": True})
                        try:
                            return self._json({"models": providers.list_models(providers.get(pid))})
                        except providers.ProviderError as e:
                            return self._json({"models": [], "error": str(e)})
                    if u.path == "/api/ai/caps":
                        q = parse_qs(u.query)
                        pid, model = (q.get("id") or [""])[0], (q.get("model") or [""])[0]
                        if app.session.demo and model not in ("llama3.1:8b", "qwen2.5:14b", "mistral-nemo:12b", "gemma3:4b"):
                            return self._json({"ok": False, "error": f"Ollama doesn't have the model '{model}'.  (download it with:  ollama pull {model}) "
                                                                     "It has: gemma3:4b, llama3.1:8b, mistral-nemo:12b, qwen2.5:14b"})
                        if app.session.demo:
                            return self._json({"ok": True, "caps": {"tools": "gemma" not in model, "vision": "llava" in model,
                                                                    "context": 4096 if "mistral" in model else 131072, "source": "demo"}})
                        try:
                            return self._json({"ok": True, "caps": providers.preflight(providers.get(pid), model)})
                        except providers.ProviderError as e:
                            return self._json({"ok": False, "error": str(e)})
                    if u.path == "/api/backends":
                        return self._json(app.session.backend_info())
                    if u.path == "/api/context":
                        return self._json({"text": core.CONTEXT_PATH.read_text(encoding="utf-8") if core.CONTEXT_PATH.exists() else ""})
                    if u.path == "/api/connectors":
                        return self._json(connectors_info())
                    if u.path == "/api/ui":
                        return self._json(appearance.get())
                    if u.path == "/api/notify":
                        return self._json(notify_info())
                    if u.path == "/api/playbooks":
                        return self._json({"playbooks": playbooks.listing()})
                    if u.path == "/api/discord":
                        return self._json(app.discord.info())
                    if u.path == "/api/files/diff":
                        return self._json(core.file_version_diff(int(parse_qs(u.query).get("id", ["0"])[0])))
                    if u.path == "/api/skills":
                        return self._json({"skills": skills.listing(), "active": skills.active()})
                    if u.path == "/api/skills/read":
                        return self._json({"text": skills.read_md(parse_qs(u.query).get("id", [""])[0])})
                    if u.path == "/api/commands":
                        return self._json({"catalog": commands.catalog(), "custom": commands.custom()})
                    if u.path == "/api/app_settings":
                        return self._json({**app_settings(), "blender_where": {"host": core.BLENDER_HOST, "port": core.BLENDER_PORT}})
                    if u.path == "/api/ui/wallpaper":
                        wp = appearance.wallpaper_file()
                        if not wp:
                            return self._json({"error": "no wallpaper"}, 404)
                        import images as _im
                        return self._send(200, wp.read_bytes(), _im.sniff(wp.read_bytes()[:16])[1])
                    if u.path == "/api/chats":
                        return self._json(chats.overview() if chats.enabled() else {"active": "", "chats": [], "projects": [], "disabled": True})
                    self._json({"error": "not found"}, 404)
                except ValueError as e:
                    self._json({"error": core.redact(str(e))}, 400)
                except Exception as e:
                    self._json({"error": core.redact(f"{type(e).__name__}: {e}")}, 500)

            def do_POST(self):
                u = urlparse(self.path)
                if not self._guard(True):
                    return
                try:
                    if u.path == "/api/ui/wallpaper":  # raw image bytes
                        return self._json(appearance.set_wallpaper(self._raw_body(appearance.MAX_WALLPAPER + 1)))
                    if u.path == "/api/skills/zip":  # raw .zip bytes -> preview; nothing is added until you confirm
                        return self._json(app.stage_skill(skills.files_from_zip(self._raw_body(skills.MAX_TOTAL + 1_000_000)), "uploaded zip"))
                    if u.path == "/api/image":  # raw bytes (screenshots are too big for the 1 MB JSON limit)
                        q = {k: v[0] for k, v in parse_qs(u.query).items()}
                        meta = images.add("human", self._raw_body(images.MAX_BYTES * 3), q.get("name", ""), q.get("note", ""))
                        return self._json({"ok": True, "image": meta})
                    b, s = self._body(), app.session
                    path = u.path
                    if path == "/api/start":
                        s.start(b)
                    elif path == "/api/pause":
                        s.pause()
                    elif path == "/api/resume":
                        s.resume()
                    elif path == "/api/files/undo":
                        nid = core.undo_file_version(int(b["id"]), "human", force=bool(b.get("force")))
                        return self._json({"ok": True, "id": nid, "changes": core.file_versions(limit=40)})
                    elif path == "/api/files/recovery_seen":
                        s.file_recovery = None
                    elif path == "/api/limit/cancel":
                        s.cancel_auto_resume()
                    elif path == "/api/step":
                        s.step()
                    elif path == "/api/stop":
                        s.stop()
                    elif path == "/api/halt":
                        s.emergency_stop()
                    elif path == "/api/backend_test":
                        return self._json(app.session.backend_test(b.get("agent"), b.get("model")))
                    elif path == "/api/perms":
                        core.set_perm(b.get("agent"), b.get("group"), bool(b.get("allowed")))
                    elif path == "/api/budget":
                        core.set_budget(b.get("limit_usd"), b.get("prices"))
                    elif path == "/api/recover":
                        s.recover_action(b.get("action"))
                    elif path == "/api/extend":
                        s.extend(b.get("turns", 6))
                    elif path in ("/api/say", "/api/command") and commands.is_command(str(b.get("text", "")).strip()):
                        try:  # slash commands: only ever from you (this page or a paired phone), never from the AIs
                            return self._json(commands.run(app, str(b.get("text", "")).strip(), images=b.get("images")))
                        except commands.CommandError as e:
                            return self._json({"ok": False, "error": str(e)}, 400)
                    elif path.startswith("/api/skills/"):
                        return self._json(app.skills_post(path.rsplit("/", 1)[1], b))
                    elif path == "/api/commands":
                        if b.get("delete"):
                            commands.delete_custom(str(b["delete"]))
                        else:
                            commands.save_custom(b.get("name"), b.get("template"), b.get("description", ""), b.get("old_name"))
                        return self._json({"catalog": commands.catalog(), "custom": commands.custom()})
                    elif path == "/api/say":
                        text = str(b.get("text", "")).strip()
                        if text.startswith("//"):
                            text = text[1:]  # "//word" sends a literal "/word" to the AIs
                        ids = [str(i) for i in (b.get("images") or [])][:images.MAX_PER_MESSAGE]
                        for i in ids:
                            if images.meta(i)["by"] != "human":
                                raise ValueError("you can only attach images you uploaded")
                        if not text and not ids:
                            raise ValueError("say something first")
                        if ids:
                            core.append_transcript("human", text[:8000] or "(sent an image)", images=[{"id": i, "name": images.meta(i)["name"]} for i in ids])
                        else:
                            core.append_transcript("human", text[:8000])
                        return self._json({"ok": True, "note": s.send_now() if b.get("now") else s.nudge()})
                    elif path == "/api/push":  # the "Send now" button under a message you already sent
                        return self._json({"ok": True, "note": s.send_now()})
                    elif path == "/api/task":
                        t = str(b.get("task", "")).strip()
                        core.mutate_state(lambda st: st.__setitem__("task", t))
                    elif path == "/api/disagreement":
                        core.decide_disagreement(int(b.get("id", 0)), str(b.get("pick", "")), str(b.get("text", "")))
                        return self._json({"ok": True, "note": s.nudge()})
                    elif path == "/api/approve":
                        if not core.decide_approval(int(b["id"]), bool(b["approved"])):
                            raise ValueError("That request was already answered or has expired.")
                    elif path == "/api/mode":
                        core.set_mode(b.get("mode"))
                    elif path == "/api/models":
                        s.set_models(b.get("claude"), b.get("chatgpt"))
                    elif path == "/api/style":
                        core.set_style(b.get("style", "chat"), b.get("lead"))
                    elif path == "/api/board":
                        act = b.get("action")
                        if act == "add":
                            core.board_add_task("human", b.get("title", ""), b.get("owner", ""), b.get("depends_on", ""))
                        elif act == "update":
                            core.board_update_task("human", b["task_id"], b.get("status", ""), b.get("owner", ""), b.get("note", ""))
                        elif act == "delete":
                            core.board_delete_task(b["task_id"])
                        else:
                            raise ValueError("unknown board action")
                    elif path == "/api/context":
                        core.CONTEXT_PATH.write_text(str(b.get("text", ""))[:20000], encoding="utf-8")
                    elif path.startswith("/api/memory/"):
                        return self._json(self._memory_post(path, b))
                    elif path == "/api/keys":
                        save_keys(b.get("anthropic", ""), b.get("openai", ""))
                    elif path == "/api/ai/save":
                        pid, env = providers.save(b)
                        if env:
                            _env_write(env)
                        return self._json({"ok": True, "id": pid, "profiles": providers.profiles()})
                    elif path == "/api/ai/remove":
                        _env_write(providers.remove(str(b.get("id", ""))))
                        return self._json({"ok": True, "profiles": providers.profiles()})
                    elif path == "/api/ai/test":
                        return self._json(providers.test(str(b.get("id", ""))))
                    elif path == "/api/connectors":
                        return self._json(connectors_save(b))
                    elif path == "/api/ui":
                        return self._json(appearance.save(b))
                    elif path == "/api/notify":
                        return self._json(notify_save(b))
                    elif path == "/api/app_settings":
                        return self._json(app_settings_save(b))
                    elif path == "/api/playbooks":
                        a_ = b.get("action")
                        if a_ == "save":
                            pb = playbooks.save(b.get("name"), b.get("setup") or {}, b.get("description", ""), b.get("id") or None)
                            return self._json({"ok": True, "id": pb["id"], "playbooks": playbooks.listing()})
                        if a_ == "delete":
                            playbooks.delete(str(b.get("id", "")))
                            return self._json({"ok": True, "playbooks": playbooks.listing()})
                        raise ValueError("unknown action")
                    elif path == "/api/discord":
                        return self._json(discord_post(app, b))
                    elif path == "/api/blender/test":
                        return self._json(blender_test())
                    elif path == "/api/notify/test":
                        return self._json({"ok": True, "to": notify.send("test", "Test notification from AI Bridge. If you can read this, it works.", force=True, wait=True),
                                           "log": notify.SENT_LOG[-1:]})
                    elif path.startswith("/api/chats/") or path.startswith("/api/projects/"):
                        return self._json(self._chats_post(path, b))
                    elif path == "/api/connectors/paste":
                        return self._json(connectors_paste(b))
                    elif path == "/api/connectors/add":
                        return self._json(connectors_add(b))
                    elif path == "/api/connectors/remove":
                        return self._json(connectors_remove(b))
                    elif path == "/api/connectors/test":
                        import connectors
                        return self._json(connectors.test(str(b.get("name", ""))))
                    elif path == "/api/open_folder":
                        open_folder(core.WORKSPACE)
                    else:
                        return self._json({"error": "not found"}, 404)
                    self._json({"ok": True})
                except (ValueError, KeyError, TypeError) as e:
                    self._json({"error": core.redact(str(e) or type(e).__name__)}, 400)
                except Exception as e:
                    self._json({"error": core.redact(f"{type(e).__name__}: {e}")}, 500)

        return Handler


# --------------------------------------------------------------------------- entry point
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if "--mcp-server" in sys.argv:  # hidden: the packaged .exe doubles as the tool server that subscription-mode CLIs launch
        sys.argv.remove("--mcp-server")
        import bridge_server
        bridge_server.main()
        return
    ap = argparse.ArgumentParser(description="AI Bridge app")
    ap.add_argument("--port", type=int, default=8750)
    ap.add_argument("--demo", action="store_true", help="scripted fake AIs: no API keys or credits needed")
    ap.add_argument("--window", action="store_true", help="open in its own native window (needs: pip install pywebview)")
    ap.add_argument("--no-browser", action="store_true", help="just run the server")
    ap.add_argument("--selfcheck", action="store_true", help="check the AI libraries import (useful for the packaged .exe)")
    a = ap.parse_args()

    if a.selfcheck:
        import anthropic
        import mcp  # noqa: F401  (the tool server subscription mode launches)
        import openai
        try:
            import discord
            dv = discord.__version__
        except ImportError:
            dv = "MISSING (pip install discord.py)"
        print("anthropic", anthropic.__version__, "| openai", openai.__version__, "| discord", dv, "| mcp ok | ui:", (UI_DIR / "index.html").exists())
        return

    app = App(use_chats=True, demo=a.demo, port=a.port)
    app.discord.start()  # only does anything once you've saved a bot token (Settings > Discord)
    print(f"AI Bridge is running at {app.url}   (Ctrl+C to quit)")
    print(f"Workspace: {core.WORKSPACE}")
    if a.demo:
        print("DEMO MODE: scripted fake AIs, no API calls.")
    if a.window:
        try:
            import webview
        except ImportError:
            print("pywebview isn't installed (pip install pywebview), opening your browser instead.")
        else:
            app.serve_in_thread()
            webview.create_window("AI Bridge", app.url, width=1320, height=860, min_size=(900, 600))
            webview.start()
            return
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(app.url)).start()
    try:
        app.httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        app.session.stop()
        app.discord.stop(wait=False)


if __name__ == "__main__":
    main()
