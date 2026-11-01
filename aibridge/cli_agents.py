"""Subscription mode: run Claude and ChatGPT through the official CLIs you're already signed in to
(Claude Code and the OpenAI Codex CLI), so usage comes out of your Claude / ChatGPT plan instead of per-token API billing.

How it stays safe: the CLI gets NO built-in file/shell tools (Claude) or a read-only sandbox (Codex). Its only way to
change anything is the same guarded tools every other path uses, served by bridge_server.py over MCP, so the approval gate,
per-AI permissions, journal, budget checks and emergency stop all apply exactly as in API mode.

API keys are stripped from the CLI's environment on purpose: if ANTHROPIC_API_KEY / OPENAI_API_KEY are present the CLIs can
switch to billing your API account, which is the thing this mode exists to avoid.
"""
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import core

HERE = Path(__file__).resolve().parent
# anything that could flip the CLI onto pay-per-token billing (or a different provider) is removed from its environment
STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
             "CLAUDE_CODE_USE_FOUNDRY", "OPENAI_API_KEY", "CODEX_API_KEY")
SERVER_NAME = "aibridge"
TURN_TIMEOUT = float(os.getenv("BRIDGE_CLI_TIMEOUT", "900"))  # seconds one CLI turn may take (includes waiting on your approvals)

INSTALL_HINT = {
    "claude": "Install Claude Code (https://claude.com/product/claude-code), then run:  claude auth login",
    "chatgpt": "Install the Codex CLI (npm i -g @openai/codex), then run:  codex login   (choose Sign in with ChatGPT)",
}


def cli_command(agent):
    """The program to run as a list (so tests can point it at a fake), or None if it isn't installed.
    CLAUDE_BIN / CODEX_BIN may be a path, or a JSON list like ["python", "fake.py"] (used by the self-test)."""
    env, default = ("CLAUDE_BIN", "claude") if agent == "claude" else ("CODEX_BIN", "codex")
    val = os.getenv(env) or default
    if val.strip().startswith("["):
        try:
            lst = json.loads(val)
            return [str(x) for x in lst] if lst else None
        except ValueError:
            return None
    found = shutil.which(val)
    return [found] if found else None


def clean_env(extra=None):
    env = {k: v for k, v in core.safe_env().items() if k not in STRIP_ENV}  # no keys/tokens: the tool server reads .env itself
    env.update(extra or {})
    return env


def _popen(cmd, **kw):
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kw["start_new_session"] = True  # own process group, so a stop can end the CLI and everything it started
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", **kw)


def login_status(agent):
    """(found, logged_in, detail). Never raises."""
    base = cli_command(agent)
    if not base:
        return False, False, INSTALL_HINT[agent]
    cmd = base + (["auth", "status"] if agent == "claude" else ["login", "status"])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=clean_env(), encoding="utf-8", errors="replace")
    except Exception as e:
        return True, False, f"couldn't check login: {type(e).__name__}: {e}"
    out = (r.stdout or r.stderr or "").strip()
    if agent == "claude":
        try:
            d = json.loads(r.stdout)
            return True, bool(d.get("loggedIn")), "signed in" if d.get("loggedIn") else INSTALL_HINT[agent]
        except ValueError:
            pass
    ok = r.returncode == 0 and "not logged in" not in out.lower()
    return True, ok, (out.splitlines()[-1] if out else "") if ok else INSTALL_HINT[agent]


def server_command(side, blender):
    """The command a CLI should run to get the guarded tools (an MCP server over stdio)."""
    if getattr(sys, "frozen", False):  # packaged .exe: the same exe has a hidden --mcp-server mode
        base = [sys.executable, "--mcp-server"]
    else:
        base = [sys.executable, str(HERE / "bridge_server.py")]
    cmd = base + ["--side", side, "--mode", core.effective_mode(), "--no-ask"]
    return cmd + (["--blender"] if blender else [])


def server_env():
    return {"BRIDGE_WORKSPACE": str(core.WORKSPACE), "BRIDGE_DATA": str(core.DATA), "BRIDGE_FOLLOW_APP_MODE": "1", "PYTHONIOENCODING": "utf-8"}


class CLIAgent:
    """Common machinery. Subclasses build the command line; this runs it, watches it, and reads the answer."""
    billing = "subscription"
    name = ""

    def __init__(self, model=None, timeout=None, seat=None):
        self.model = model
        self.timeout = timeout or TURN_TIMEOUT
        self.name = seat or self.kind  # the seat it sits in (its tools and turns are that seat's), whichever CLI it is
        self.blender = False

    def configure(self, blender=False):
        self.blender = bool(blender)

    def build(self, td, system, prompt):  # -> (cmd, stdin_text, out_file or None)
        raise NotImplementedError

    def parse(self, stdout, out_file):  # -> reply text
        raise NotImplementedError

    def turn(self, system, prompt, tools=None, max_steps=None):
        base = cli_command(self.kind)
        if not base:
            raise RuntimeError(f"{core.PRETTY[self.name]}'s CLI wasn't found. {INSTALL_HINT[self.kind]}")
        core.preflight(self.name)
        with tempfile.TemporaryDirectory(prefix="aibridge_cli_") as td:
            cmd, stdin_text, out_file = self.build(td, system, prompt)
            cmd = base + cmd[1:]
            proc = _popen(cmd, cwd=str(core.WORKSPACE), env=clean_env({"MCP_TOOL_TIMEOUT": str(int(self.timeout * 1000))}))
            with core._PROCS_LOCK:
                core._PROCS.add(proc)
                core._PROC_OWNER[proc] = self.name
            try:
                try:
                    out, err = proc.communicate(stdin_text, timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    core.kill_proc_tree(proc)
                    proc.communicate()
                    raise RuntimeError(f"{core.PRETTY[self.name]}'s CLI took longer than {int(self.timeout)}s and was ended.")
            finally:
                with core._PROCS_LOCK:
                    core._PROCS.discard(proc)
                    core._PROC_OWNER.pop(proc, None)
            if core.halt_reason():
                raise core.Halted(core.halt_reason(), "halt")
            if core.interrupted(self.name):
                raise core.Interrupted("ended by Send now")
            if proc.returncode != 0:
                tail = (err or out or "").strip()[-600:]
                hint = ("  " + INSTALL_HINT[self.kind]) if any(w in tail.lower() for w in ("login", "auth", "sign in", "credential", "401")) else ""
                raise RuntimeError(f"{core.PRETTY[self.name]}'s CLI exited with code {proc.returncode}: {tail}{hint}")
            return self.parse(out, out_file)


class ClaudeCLIAgent(CLIAgent):
    kind = name = "claude"
    provider = "Claude"

    def build(self, td, system, prompt):
        sysfile, cfgfile = Path(td) / "system.txt", Path(td) / "mcp.json"
        sysfile.write_text(system, encoding="utf-8")
        srv = server_command(self.name, self.blender)
        cfgfile.write_text(json.dumps({"mcpServers": {SERVER_NAME: {"command": srv[0], "args": srv[1:], "env": server_env()}}}),
                           encoding="utf-8")
        cmd = ["claude", "-p", "--output-format", "json", "--append-system-prompt-file", str(sysfile),
               "--mcp-config", str(cfgfile), "--strict-mcp-config",
               "--tools", "",                           # no built-in Bash / Edit / Write / Read: the guarded MCP tools are all it has
               "--allowedTools", f"mcp__{SERVER_NAME}",
               "--permission-mode", "dontAsk",           # anything not explicitly allowed is refused, never asked
               "--no-session-persistence"]
        if self.model:
            cmd += ["--model", self.model]
        return cmd, prompt, None

    def parse(self, stdout, out_file):
        try:
            d = json.loads(stdout)
        except ValueError:
            return (stdout or "").strip()
        if not isinstance(d, dict):
            return (stdout or "").strip()
        u = d.get("usage") or {}
        core.record_usage(self.name, core._num(u.get("input_tokens")) + core._num(u.get("cache_creation_input_tokens"))
                          + core._num(u.get("cache_read_input_tokens")), core._num(u.get("output_tokens")))
        if d.get("is_error"):
            raise RuntimeError("Claude Code reported an error: " + str(d.get("result") or d.get("subtype") or "unknown")[:500])
        return str(d.get("result") or "").strip()


_CODEX_BLOCKED = re.compile(r"user cancelled MCP tool call|blocked because it requires approval|approval policy is .?never", re.I)


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
MAX_GEN_PER_TURN = 8
IMAGE_MODES = ("ask", "allowed", "off")
IMAGE_RULES = {
    "ask": ("IMAGES: you can make images with your built-in image generation ($imagegen), but ONLY when the human explicitly asks "
            "for an image in this conversation. Images may use up their ChatGPT plan's allowance much faster than text, so never make one on your "
            "own initiative or as a 'nice to have'. Finished images appear in the chat automatically (Claude can see them too); "
            "say in one line what you made."),
    "allowed": ("IMAGES: you can make images with your built-in image generation ($imagegen) when one would really help the work "
                "(concept art, UI mockups, icons, textures, reference sheets). Each one may use up the human's ChatGPT plan allowance "
                "much faster than text: make only what's needed, one at a time, no variations unless asked. Finished images appear in the "
                "chat automatically (Claude can see them too); say in one line what you made."),
    "off": "IMAGES: the human has switched image generation off. Don't generate images; describe what you'd make instead.",
}


def image_mode():
    m = (core._read_json(core.DATA / "app_settings.json", {}) or {}).get("image_gen", "ask")
    return m if m in IMAGE_MODES else "ask"


def generated_dir():
    """Where Codex saves what its image generation makes: $CODEX_HOME/generated_images (default ~/.codex)."""
    home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    return Path(home) / "generated_images"


def _gen_snapshot():
    d = generated_dir()
    out = {}
    if d.is_dir():
        for p in d.rglob("*"):
            if p.is_file() and p.suffix.lower() in IMAGE_EXT:
                try:
                    st = p.stat()
                    out[str(p)] = (st.st_mtime_ns, st.st_size)
                except OSError:
                    pass
    return out


class CodexCLIAgent(CLIAgent):
    kind = name = "chatgpt"
    provider = "ChatGPT"

    def turn(self, system, prompt, tools=None, max_steps=None):
        """A normal Codex turn, plus: pick up any images it generated during the turn (new files in generated_images)."""
        before = _gen_snapshot()
        self.new_images = []
        text = super().turn(system, prompt, tools, max_steps)
        after = _gen_snapshot()
        fresh = [p for p, sig in after.items() if before.get(p) != sig]
        mentioned = set(re.findall(r"[^\s'\"`()<>]*generated_images[^\s'\"`()<>]*\.(?:png|jpe?g|webp|gif)", text or "", re.I))
        fresh.sort(key=lambda p: (0 if any(Path(m).name == Path(p).name for m in mentioned) else 1, after[p][0]))
        self.new_images = [Path(p) for p in fresh[:MAX_GEN_PER_TURN]]
        self.extra_images = max(0, len(fresh) - MAX_GEN_PER_TURN)
        return text

    def take_new_images(self):
        imgs, self.new_images = getattr(self, "new_images", []), []
        return imgs

    def build(self, td, system, prompt):
        system = (system + "\n\n" + IMAGE_RULES[image_mode()] + "\nQUIET RULE: never comment in the chat on skills or tools you are NOT "
                  "using (no 'the image skill doesn't apply here', no 'I'm not invoking X'). Only mention a skill when you actually use it.")
        out_file = Path(td) / "last.txt"
        srv = server_command(self.name, self.blender)
        q = json.dumps  # JSON strings are valid TOML strings
        imgs = [str(p) for p in core.turn_images()]  # pictures the human just attached; the flag must be followed by another flag, so -s comes next
        cmd = ["codex", "exec"] + [x for p in imgs for x in ("-i", p)] + ["-s", "read-only",     # its own shell can read but never write; changes go through the guarded tools
               "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--color", "never",
               "-C", str(core.WORKSPACE), "-o", str(out_file),
               "-c", f"mcp_servers.{SERVER_NAME}.command={q(srv[0])}",
               "-c", f"mcp_servers.{SERVER_NAME}.args={q(srv[1:])}",
               "-c", f"mcp_servers.{SERVER_NAME}.env={{{', '.join(f'{k}={q(v)}' for k, v in server_env().items())}}}",
               "-c", f"mcp_servers.{SERVER_NAME}.tool_timeout_sec={int(self.timeout)}",
               "-c", f"mcp_servers.{SERVER_NAME}.startup_timeout_sec=60",
               # `codex exec` has nobody to ask, so without this it cancels EVERY MCP call (even reads). This server is our own
               # guarded tool server (approval gate, permissions, journal, emergency stop all live inside it), so Codex's
               # own per-call prompt is redundant. Codex's shell stays read-only either way (-s read-only above).
               "-c", f'mcp_servers.{SERVER_NAME}.default_tools_approval_mode="approve"']
        if self.model:
            cmd += ["-m", self.model]
        cmd.append("-")  # the prompt comes on stdin (no command-line length limit)
        return cmd, f"[Your instructions]\n{system}\n\n[The conversation so far]\n{prompt}", out_file

    def parse(self, stdout, out_file):
        try:
            text = Path(out_file).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            text = ""
        core.record_usage(self.name, 0, 0)  # counts the call; the plan, not tokens, is what's being used
        if _CODEX_BLOCKED.search((stdout or "") + "\n" + text):
            raise RuntimeError(
                "Codex refused to run its tools: it treats MCP tool calls as needing approval, and `codex exec` has nobody "
                "to ask. AI Bridge already tells it to auto-approve its own guarded tool server, so your Codex is probably "
                "too old to honor that setting. Update it (npm i -g @openai/codex), then press Test connection again.")
        return text or (stdout or "").strip()


def make_cli_agent(kind, model=None, seat=None):
    """kind: which CLI ('claude' = Claude Code, 'chatgpt' = Codex); seat: which of the two places it sits in."""
    return ClaudeCLIAgent(model=model, seat=seat) if kind == "claude" else CodexCLIAgent(model=model, seat=seat)
