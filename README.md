# AI Bridge: Claude + ChatGPT, talking and building together

A standalone desktop-style app that runs **Claude** and **ChatGPT** through their APIs so they can chat with
each other freely and collaborate on real tasks (coding, planning, writing, whatever). Both read the same
transcript and shared state, share one task board, and use the same tools. You watch, jump in, and approve.

Apps like Blender, Unity, Godot, Roblox Studio, Figma, GitHub, Obsidian or a web browser are **optional add-ons** (Settings > Connectors). Without them, this is a general AI-to-AI workspace.

> It connects the two **APIs**, not your open chatgpt.com / claude.ai tabs (those can't be plugged into outside
> code). The shared transcript + state files give the two models continuity.

## Install (Windows)

Download **AI-Bridge-Setup-<version>.exe** and run it. It installs just for you (no admin prompt), can install Claude Code,
Codex and Ollama for you from their makers' official installers, and opens AI Bridge in its own window. A short setup wizard
then connects your AIs (install, sign in, test), picks a look, can bring over an older AI Bridge folder (it copies, never moves),
and offers Discord and add-ons. Everything you can do is explained in **How to use** (the ? button, or Settings > How to use).

Your chats, keys and settings live in `%LOCALAPPDATA%\AI Bridge`, separate from the program, so updates never touch them.
Closing the window can keep it running in the tray (sessions and Discord carry on), and it can start with Windows.

The installer isn't code-signed yet, so Windows SmartScreen may warn the first time: More info > Run anyway.

Build the installer yourself: `packaging\build_windows.bat` (needs Python 3.12 and the free Inno Setup 6). Pushing a `v*` tag
builds it on GitHub Actions too.

## Run from source (Windows)

1. Install Python 3.10+ from python.org (tick "Add python.exe to PATH").
2. Double-click **`run_app.bat`**. First run builds a private environment and installs packages, then opens the app in your browser.
3. Pick how each AI connects (next section): your **subscriptions** (no API keys, no per-token bills) or **API keys**.
4. Click **New session**, pick a style, models and safety mode, hit start.

Want to poke around for free first? `run_app.bat --demo` runs scripted stand-in AIs: no keys, no subscriptions, no cost, and it
includes a fake approval request so you can see the flow.

From a terminal instead: `python aibridge/app.py` (add `--demo` for the stand-in AIs, `--window` for its own window + tray).

## What's in it (quick tour)

- **Chats and projects:** each chat has its own transcript, board, files and memory. `/new`, `/switch`, `/fork`.
- **Any two AIs:** each seat can be Claude (your plan or API key), ChatGPT (your plan or API key), or any OpenAI-compatible
  server: Ollama or LM Studio (free, on your PC), Gemini, OpenRouter or a custom URL (Settings > AI models > Other AIs). Pick
  the model per seat and give each a name. AI Bridge checks the server and model before starting, shows what the model can
  do (tools, images, memory size), and pauses safely if a local server goes away mid-session.
- **Slash commands** in the message box: `/claude`, `/gpt`, `/pause`, `/turns +5`, `/undo`, `/decide`, `/play`, `/report`… (`/help`).
- **Skills:** instruction packs (SKILL.md) you import or write, switched on per chat. The AIs can propose new ones; you always approve.
- **Crash-safe files:** every AI file write is backed up and atomic; Files > Recent changes shows diffs and undoes a change.
- **Discord:** your own bot (Settings > Discord & alerts). Approval cards with buttons, the conversation mirrored in a thread
  per chat (type there to talk to the AIs), notifications, and `/status /tasks /summary /pause /stop /emergency /undo /cmd`.
  Only the Discord account that linked it is obeyed.
- **Disagreements:** when the two AIs can't settle something they raise it instead of arguing; you get both sides next to each
  other and pick one (or say what you want). In the app, Discord, or `/decide 2 claude`.
- **Report cards:** when a session ends you get what got done: tasks, files, decisions, approvals, tokens and cost per AI, and what's
  still open. Counted by the app, no AI call. `/report` shows the last one.
- **Playbooks:** saved setups (task with {blanks}, style, safety, turns, seats, skills). Pick one in New session or
  `/play Bug hunt: <the error>`. Three starters included.

## Use your subscriptions instead of paying per token

By default an API key bills per token, separate from your Claude / ChatGPT plans. **Subscription mode** avoids that by running each
AI through the official CLI you sign in to with your normal account:

| AI | install once | sign in once |
|---|---|---|
| Claude | Claude Code (https://claude.com/product/claude-code) | `claude auth login` |
| ChatGPT | `npm i -g @openai/codex` | `codex login` and choose **Sign in with ChatGPT** |

Then in **New session > How each AI connects** pick **My plan** for either or both (you can mix: one on a plan, one on an API key).
The app checks each CLI is installed and signed in, and **Settings > How each AI connects > Test connection** runs one tiny real
turn so you see any problem with the real error text before you start.

What's true about it, so you aren't surprised:
- **What the vendors say.** Anthropic's help page on this (updated Oct 7, 2026) says `claude -p` and the Agent SDK still draw from your
  plan's limits, that Max/Team plans also get monthly API credits, and that an earlier planned change was paused ("for now, nothing has
  changed"), so it could change again. For Codex, OpenAI's docs show "Sign in with ChatGPT" and `codex exec` for scripted use; I couldn't
  confirm from their docs which plans cover non-interactive use, so treat that as something your Test connection button settles.
- **It still uses your plan's limits.** Two AIs chatting all day can hit a rate limit. Turn counts, not dollars, are shown.
- **API keys are removed from the CLI's environment on purpose.** Claude Code can silently switch to billing your API account when
  `ANTHROPIC_API_KEY` is set (that has caught people out with big bills), so this app never lets a CLI see your keys.
- **Same safety.** The CLI gets *no* built-in tools in Claude's case (Codex runs in a read-only sandbox) and reaches your workspace only through this
  app's guarded tools (served to it over MCP). So approvals, per-AI permissions, the journal, emergency stop (which also kills the running
  CLI), and the live safety mode all apply exactly as in API mode. It follows the app's *current* mode, and the more restrictive of its
  starting mode and the live one wins.
- **Codex and tool approval.** `codex exec` has nobody to ask, so by default it cancels every MCP tool call (even reads) and the AI just says
  it was "blocked". AI Bridge passes `default_tools_approval_mode="approve"` for its own tool server only, which is safe because every call still
  goes through this app's approval gate, permissions and emergency stop. Codex's own shell stays read-only. Test connection now proves tools
  work: the AI must read a random code from a file and repeat it back.
- **Differences.** Each turn is slower than an API call (a CLI starts every time). The live tool-call log in the Activity tab is API-mode
  only; the risky-action journal and approvals still show. Screenshots from Blender may not reach the CLI models. Claude Code still loads
  your own Claude settings (it can't use your plan with `--bare`), and Codex's read-only sandbox can still *read* files on your PC, it
  just can't write them.
- **Verified for real on Windows:** Claude Code and Codex, both signed in to their plans, ran full sessions through the guarded tools:
  approvals (approve and deny), a review where Codex re-ran Claude's fix to check it, a disagreement decided by the human, and report cards.

Prefer API keys? Put them in **Settings > API keys** (stored in a local `.env`) and choose **API key** for that AI.

## Choosing models

Each AI has its own model. Set them in the New session dialog or change them live in **Settings > Models > Apply**
(takes effect on the next turn). Click **Fetch** to list what your keys can actually use.

- Claude presets: `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-fable-5-1`
- OpenAI presets: `gpt-6.1-sol`, `gpt-6-sol`, `gpt-5.6-sol`, `gpt-6-luna`, `gpt-5.6-terra`, `gpt-5.6-luna`
- Any other ID works if your account has it. Caveat: models that only support OpenAI's Responses API for tool use
  (reportedly `gpt-6-astra`) aren't supported yet. Each message in the chat shows which model wrote it.

## Collaboration styles

| style | what it does |
|---|---|
| Chat | Free conversation, all tools available |
| Discussion | Talk and plan only; write/run/scene tools are removed |
| Collaboration | Split the work, claim tasks on the board, build together |
| Review | One builds, the other critiques and signs off |

Pick who leads (Claude, ChatGPT, or neither). The **task board** has owners, status, dependencies, and a decision log.
The coordinator (pause, resume, step, stop, add turns, [DONE] detection) is the app itself, independent of either AI.

## Safety modes (you stay in charge)

| mode | what happens |
|---|---|
| `approval` (**default**) | Every **file write, script run and Blender change** pops an approval card showing exactly what's about to happen. Unanswered = no. |
| `autonomous` | No prompts. Fine for low-stakes stuff once you trust the setup. |
| `readonly` | They can talk, read, inspect, review. They can't modify anything. |

One shared gate covers both AIs, so neither can dodge your "no" by asking the other to do it. Timeout
(`BRIDGE_APPROVAL_TIMEOUT`, default 120s), stopping the session, or no answer all count as **no**. Decisions are
logged in `state.json` under `approvals`. `run_script` runs Python **on your PC**, so read what they write.

**Project context:** put notes in the app's Context tab (saved to `workspace\CONTEXT.md`). Both models get it in
their instructions; the AIs can't edit it.

**App security:** the server listens on 127.0.0.1 only, every request needs a per-launch token, and Host/Origin are checked.

## Seeing and controlling the run

**Live activity.** Under the task bar, each AI has a card: idle / thinking / using a tool / waiting for your approval,
with the current task and elapsed seconds, plus a spend bar. The **Activity** tab lists recent tool calls (including
refused ones) and a journal of the risky ones (writes, scripts, scene changes) with their outcome.

**Talking to them.** Sending a message wakes the AIs. Running: they read it on their next turn. Paused: the next AI
answers once, then it stays paused. Finished: it gets 2 more turns. Stopped / emergency-stopped / errored: left alone,
because you ended that on purpose (use Start or +turns). A toast tells you which of these happened.

**Pause vs. emergency stop.**
- *Pause* finishes the current turn, then nothing new starts. Everything is kept; Resume or "One turn" continues.
- *Stop* finishes the current turn, then ends the session.
- *Emergency stop* (the red button, or `/emergency` in Discord) acts at once: every tool call is refused, no new
  model call starts (an in-flight call finishes, then the turn is cut), pending approvals are denied, and a late "yes"
  doesn't revive anything. It can't undo something that already ran; the journal shows what did. "+6 turns" or a new
  session lifts it on purpose; plain Resume won't.

**Cost and budget.** Token use is counted per AI from each API response and turned into dollars with the per-million
prices in Settings > Budget. **The starting prices are placeholders, not real quotes**, so enter your models' real rates
if you care about the dollars. Set a limit (also in the New session sheet) and the session
**pauses itself** when the estimate reaches it; raise the limit and Resume. The check runs before every model call, so one
in-flight call can overshoot a little. Token counts are exact as reported; dollars are estimates.

**Crash recovery.** The conversation, board, decisions and approval history are saved continuously, plus the session's
position (`workspace\session.json`). If the app dies or is closed mid-session, the next launch shows a banner with
**Resume session / Discard**. Resuming starts *paused*, never auto-runs, denies stale approval requests, and tells both
AIs which risky calls were in flight and may or may not have finished. **Those are never re-run automatically**, so
nothing executes twice. A turn that was mid-flight is lost (the AI just takes a fresh turn). A session that was in
hands-off mode comes back in "ask me first" mode.

**Tool permissions (per AI).** Settings > Tool permissions: for each AI, switch off writing files, running scripts,
editing the board/task/notes, looking at Blender, or changing Blender. It's enforced inside the tool call itself, so it
holds for the app, the MCP server and the demo AIs alike, in every safety mode, and a refused call never
reaches the approval prompt. The AI is told what's switched off and that routing around it (like asking the other AI) is
not allowed. They also can't edit the files that hold these limits (`state.json`, `approvals.json`, `session.json`,
`halt.flag`, transcript, locks, backups): `write_file` refuses them. One honest limit: `run_script` runs real Python as
you, so a script you allow can do anything you can. Permissions decide who gets the tool, approvals decide each use.

## MCP mode: let Claude Desktop / Claude Code call ChatGPT mid-conversation

Claude Desktop, `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ai-bridge": {
      "command": "C:\\path\\to\\ai-bridge\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\ai-bridge\\aibridge\\bridge_server.py", "--side", "claude"]
    }
  }
}
```

Claude Code: `claude mcp add ai-bridge -- C:\path\to\ai-bridge\.venv\Scripts\python.exe C:\path\to\ai-bridge\aibridge\bridge_server.py --side claude`

That adds `ask_chatgpt` plus the shared tools. The server can't prompt you, so run `python aibridge/approve.py` in a second
terminal (or answer in the app) to approve requests. `--side chatgpt` exposes `ask_claude` and
`--transport streamable-http` exists, but ChatGPT's connector requirements change, so check its docs first.

## Optional: Blender (Settings > Connectors, or `--blender` on the command line)

1. Turn on a Blender MCP add-on server on localhost:9876. Both kinds work, and AI Bridge detects which one it's talking to:
   the **official Blender Lab MCP add-on** (Blender 5.1+; enable it in Preferences > Add-ons and start its server) or
   the community **ahujasid/blender-mcp** add-on (N-panel > BlenderMCP > Connect).
2. In Settings > Connectors, switch Blender on (and Test it). Every new session then gets the Blender tools. (If detection ever guesses wrong, set `BLENDER_PROTOCOL=official` or `legacy` in `.env`.)

The official add-on only runs Python, so AI Bridge turns its four Blender actions (scene info, object info, viewport
screenshot, run code) into small `bpy` snippets. The snippets were checked against a real Blender 5.2.2 with that add-on.

Enforced in code: single-writer lock; `propose_change` then the *other* AI must `review_proposal` before
`apply_proposal` (nobody approves their own); `.blend` backup before every apply; `flag_result` / `rollback`
(after a rollback use **File > Save As**); both AIs can see viewport screenshots; proposal code with `subprocess`,
`os.remove`, `eval(` etc. is refused (a seatbelt, not a sandbox).

## Project memory (shared long-term notes)

Claude and ChatGPT share four plain-Markdown files per project, so a new session can build on earlier ones without re-reading transcripts. It is local only (a `memory/` folder in your workspace, which "Start fresh" never wipes) and works the same on your Claude Pro / ChatGPT Plus connections and on API keys. It replaces the need for an Obsidian link.

**Set up:** open the **Memory** tab, click *New project*, give it a name and goals. That project is now the one both AIs use. They cannot pick or create projects, or name any path; they only ever see the active project and its four files: `PROJECT.md` (goals, requirements, constraints), `DECISIONS.md`, `PROGRESS.md`, `LESSONS.md`.

**Who may change memory** (Memory tab > Permissions; global, plus an optional per-project override; the *more restrictive* wins):

| Mode | What the AIs can do |
|---|---|
| Read-only | search and read only |
| Ask first (default) | propose changes; you see the exact diff and approve or reject each one |
| Automatic | save directly; every change is versioned, logged and can be rolled back |

Automatic never overrides the global *Read-only* safety mode, the emergency stop, the spending limit, or the per-AI `memory_write` permission (Settings > permissions). It grants no file access: `write_file` and `run_script` refuse the memory folder.

**Tools both AIs have** (same code path in API mode and in subscription mode): `get_project_context`, `search_project_memory`, `read_project_memory`, `propose_memory_update` (always queued), `write_project_memory`, `get_project_progress`, `list_project_decisions`, `get_memory_history`, `restore_memory_version`.

**Loading:** when a session starts, a compact, prioritised block (goals, decisions, active work, known problems, last session) goes into both AIs' instructions, capped at the context size you set (default 5000 characters). Turn it off for one session in the New session sheet, or globally in Permissions; the tools still work.

**Entries** look like `### E12 · title` with status, author, evidence. Status is `proposed`, `verified`, `failed` or `unverified`. `verified` needs evidence, otherwise it is saved as `unverified`; an AI's claim is never shown as confirmed on its own say-so. Near-duplicates are refused. If a new note says the opposite of an old one (one has "not", the other doesn't, same topic), both are kept, flagged, with their authors. That is a keyword heuristic; it catches the obvious cases, not subtle ones.

**Memory tab:** pick/create projects, edit the four files by hand (refused if an AI changed the file since you opened it), search, review pending proposals with diffs, history with compare and one-click rollback (a rollback is a new revision, nothing is lost), session summaries, an audit log, and the permission controls. A badge on the tab counts proposals waiting for you.

**Session summaries** are saved separately in `memory/<project>/summaries/` when a session ends. They are built from recorded data (board, decisions, journal, transcript counts), not written by an AI. If the app dies mid-session, the next start files only an "interrupted" note that says no summary was generated and lists what was in flight; it never claims anything was completed.

**Reliability:** atomic writes (temp file + replace), a snapshot per revision (last 60 kept per file), a per-project lock across the app and the CLI tool servers, `base_revision` checks so two editors can't silently overwrite each other, recovery of a save interrupted halfway, and size limits (60,000 characters per file, 4,000 per entry).

**Honest limits**
- Stored notes are fed back to the AIs as data, fenced and labelled untrusted, and AI-written notes that read like instructions to a future AI ("ignore previous instructions...") are refused. That is a pattern filter, not a guarantee; you still review proposals in Ask first mode.
- Scripts are arbitrary Python on your machine. `run_script` refuses scripts that mention the memory folder, but a determined script could still write there. Such an edit is **detected** (by hash), recorded as an "external" revision, flagged to you and the AIs, and can be rolled back. It is not prevented.
- Tested with fake models and fake CLIs (including both a Claude-flavoured and a Codex-flavoured CLI reading and proposing to the same files through the real tool server). Not tested against a real Claude/Codex login.

## Screenshots and images

You can show both AIs a picture, and they can show each other.

- **You to them:** click the paperclip in the chat box, press **Ctrl+V** with a screenshot on your clipboard (Win+Shift+S, then paste), or drag an image onto the chat. A thumbnail appears; press Send. Up to 4 per message, PNG / JPEG / GIF / WebP, 5 MB each after shrinking (install Pillow with `pip install pillow` and bigger screenshots are shrunk automatically; without it, anything over 5 MB is refused with a message).
- **Them to each other (and to you):** `share_image` posts a picture into the chat under the sender's name, either one of their own screenshots (for example from `blender_screenshot`) or an image already in the chat. `list_images` and `view_image` let either AI look at any shared image. Click an image in the chat to enlarge it.
- **How it reaches each connection:** in API mode the new image is attached to the request itself. With Codex on your plan it is passed with `codex exec --image`. Claude Code on your plan looks through `view_image` (the tool server returns a real image, the same way the Blender screenshot does). In every case the prompt also tells the AI which images are new since it last spoke.
- **Safety:** the type is checked from the file's bytes, not its name (SVG is refused because it can carry script); images live in `workspace/images/`, which the AIs cannot write to with `write_file` or scripts, so they can't forge a "human" screenshot; you can't attach an image an AI made as if it were yours; and the AIs are told that text inside a picture is content to read, not an instruction. A fresh session deletes the pictures along with the old transcript. The image tools obey the emergency stop and budget like every other tool.
- **Cost:** images use tokens (API) or plan usage (subscription), so send what you need, cropped.
- **Not verified:** a real Claude Code or Codex turn looking at an image (tested with fake CLIs and the real tool server: the image comes back as a proper image part and Codex gets the flag), and how well each model reads small text in a downscaled screenshot.

## Config (.env)

See `.env.example`: API keys, `CLAUDE_MODEL`, `OPENAI_MODEL` (starting defaults), `BRIDGE_MODE`,
`BRIDGE_APPROVAL_TIMEOUT`, `BLENDER_HOST`/`BLENDER_PORT`, `BRIDGE_WORKSPACE`. Keys you enter in Settings (other AIs,
connectors, the Discord bot token) are saved here too, and are never shown to the page or the AIs.

## Project layout

```
aibridge/      the app (Python): app.py is the entry point and web server, core.py the tools, safety gate and AI loops
ui/            the page (a single index.html, no build step)
tests/         selftest.py (no keys needed), fake_cli.py, ui_smoke.js (optional, needs Node + Playwright)
packaging/     Windows build: PyInstaller spec, Inno Setup installer, build_windows.bat
docs/          TESTING.md (the real-PC checklist)
run_app.bat    double-click to run on Windows
```

Inside `aibridge/`: `cli_agents.py` (your plans, via the Claude Code / Codex CLIs) · `providers.py` (Ollama, LM Studio, Gemini,
OpenRouter, custom) · `bridge_server.py` (the MCP tool server) · `approve.py` (answer approvals from a terminal in MCP mode) ·
`connectors.py` (add-on apps over MCP) · `discord_bot.py` + `notify.py` · `chats.py` · `memory.py` · `skills.py` · `commands.py` ·
`playbooks.py` · `report.py` · `images.py` · `appearance.py` · `desktop.py` (window + tray) · `onboarding.py` (setup wizard).

## Tests

```
python tests/selftest.py          # wants "ALL <n> CHECKS PASSED"; needs no keys, Blender or Discord
node tests/ui_smoke.js <url>      # drives the real page in Chromium against `python aibridge/app.py --demo`
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before sending changes.

## License

AI Bridge · Copyright (C) 2026 Tyler Watson

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as
published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. It is
distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

In plain words: AI Bridge is free software under the **GNU General Public License v3.0** (see [LICENSE](LICENSE)): you can use, change and
share it, and if you share a changed version, it has to stay under GPL-3.0 with its source code available.

AI Bridge works with Claude, ChatGPT and the other AIs and apps it connects to, but it isn't made by or affiliated with
Anthropic, OpenAI or any of them.

## Not verified yet

Tested end to end against fake models, a fake Blender, a fake OpenAI-compatible server (standing in for Ollama), a fake Discord, and
the demo mode (`python tests/selftest.py`), including budget pauses, emergency stop, crash recovery, kill-mid-write, permission-bypass
attempts and forged Discord buttons; and for real on Windows with Claude Code + Codex on their plans and the Roblox Studio connector.
**Not** tested yet with: live Anthropic/OpenAI API keys, a real Ollama / LM Studio, a real Discord bot, a real Blender session, or a
packaged Windows build on other PCs.
