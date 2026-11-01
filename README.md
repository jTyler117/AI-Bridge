

# AI Bridge

**Two AI agents, one workspace.** AI Bridge lets two AI agents talk to each other and work together on tasks: Claude with
Codex, Claude with Claude, or Codex with DeepSeek or a local model. They plan on a shared board, write and review each other's
work, and can use other programs through MCP, while you stay in charge with approvals.

https://github.com/user-attachments/assets/cccd919c-46ff-4a34-a473-f1fb268073d6

It's a Windows desktop app. Claude and ChatGPT run through **your own plans** (via the official Claude Code and Codex apps,
signed in with your account), so there's no per-message cost. API keys and free local models work too.

## Install

1. Download **`AI-Bridge-Setup-<version>.exe`** from [Releases](../../releases) and run it.
   - It installs just for you (no admin prompt).
   - It can install **Claude Code**, **Codex** and **Ollama** for you, straight from their makers' official installers.
   - Windows SmartScreen may warn the first time because the installer isn't code-signed yet: **More info > Run anyway**.
2. AI Bridge opens in its own window and a short **setup wizard** walks you through it:
   - **Look:** Dark, Light, OLED, High contrast, Liquid Glass.
   - **Connect your AIs:** install, sign in and test each one, or add API keys or a local model.
   - **Bring your stuff over** from an older AI Bridge folder, if it finds one. It copies, never moves.
   - **Discord** (control it from your phone) and **add-ons**, both optional.
3. Press **New session**, give them a task (or pick a playbook), and press Start.

Everything the app can do is explained inside it: the **?** button, or **Settings > How to use**.

Your chats, keys and settings live in `%LOCALAPPDATA%\AI Bridge`, separate from the program, so updates never touch them.
Closing the window can keep it running in the tray (sessions and Discord carry on), and it can start with Windows.

## What it does

- **Any two AIs.** Each seat can be Claude (your plan or an API key), ChatGPT/Codex (your plan or an API key), or any
  OpenAI-compatible model: Ollama or LM Studio (free, on your PC), Gemini, OpenRouter, DeepSeek or your own server. Pick the model
  per seat and give each a name. AI Bridge checks the model before starting, shows what it can do (tools, images, memory size),
  and pauses safely if a local server goes away mid-session.
- **Five ways to work.** Free chat · Discussion (plan only, no changes) · Collaboration (split the task, then combine) ·
  Review (one builds, the other checks) · Parallel (both work at once on different tasks).
- **A shared task board** with owners, status and dependencies, plus a decision log.
- **You stay in charge.** File writes, scripts and add-on changes wait for your OK (or switch to hands-off / read-only).
  Pause, stop, or emergency stop at any time. Every file change is backed up and can be undone.
- **Disagreements.** When the two AIs can't settle something, you get both sides next to each other and make the call.
- **Report cards.** When a session ends: what got done, files changed, decisions, approvals, and what's still open.
- **Playbooks.** Saved session setups with {blanks}: *Build a feature*, *Bug hunt* and *Brainstorm* come with it.
- **Project memory.** Goals, decisions, progress and lessons carry over between sessions, per chat and globally.
- **Skills.** Instruction packs (SKILL.md) you import or write, switched on per chat.
- **Chats and projects.** Separate workspaces, each with its own conversation, board, files and memory.
- **Images.** Paste a screenshot for both AIs; they can share images with each other too.
- **Slash commands.** `/pause`, `/turns +5`, `/claude <msg>`, `/undo`, `/play`, `/decide`, `/report`… (`/help`), plus your own.
- **Discord.** Your own bot: approval cards with buttons, the chat mirrored in a thread, alerts, and commands like
  `/status /pause /stop /emergency`. Only your account is obeyed.
- **Add-ons over MCP.** Blender, Roblox Studio, Unity, Godot, GitHub, a web browser (Playwright), Figma and Obsidian are ready
  to switch on, and any other MCP server can be added by pasting its config.
- **Themes.** Dark, Light, OLED, High contrast, custom themes, and Liquid Glass with gradients or your own wallpaper.

## Connecting the AIs

### On your plans (recommended)

AI Bridge runs each AI through its official app, signed in with your normal account, so it uses your plan instead of
per-token billing. The installer and setup wizard can install both for you.

| AI | needs | sign in |
|---|---|---|
| Claude | Claude Code, and a paid Claude plan (Pro, Max, Team or Enterprise) | the wizard's **Sign in** button, or `claude auth login` |
| ChatGPT | Codex, and a paid ChatGPT plan (Plus, Pro, Business, Edu or Enterprise) | the wizard's **Sign in** button, or `codex login` |

Good to know:
- **It still uses your plan's limits.** Two AIs working all day can hit one; the session pauses and carries on by itself when the
  limit resets.
- **Your API keys are never given to the CLIs**, so they can't quietly switch to pay-per-token billing.
- **Same safety as everywhere else.** The CLIs reach your files only through AI Bridge's guarded tools, so approvals, permissions,
  the emergency stop (which also ends the running CLI) and the safety mode all apply.
- **Test connection** runs one tiny real turn, and the AI has to read a random code through AI Bridge's tools, which proves the
  connection end to end.

### API keys and other AIs

- **API keys:** Settings > AI models & sign-in. Keys are saved only on your PC (`.env` in your data folder) and are never
  shown to the page or the AIs.
- **Other AIs:** Settings > AI models > Other AIs > Add an AI: Ollama, LM Studio, Gemini, OpenRouter, or a custom URL
  (DeepSeek and other OpenAI-compatible services).
- With API keys, tokens are counted per AI and turned into an estimate with the prices in Settings. Set a spending limit and
  the session pauses when it's reached.

## Safety

| mode | what happens |
|---|---|
| **Ask me first** (default) | Every file write, script run and add-on change shows an approval card with exactly what's about to happen. No answer counts as no. |
| **Hands-off** | No prompts. For low-stakes work once you trust the setup. |
| **Read-only** | They can talk, read and review, but can't change anything. |

- **One gate covers both AIs**, so neither can get around your "no" by asking the other.
- **Per-AI permissions** (Chat settings) can switch tools off for one AI: writing files, running scripts, editing the board,
  Blender. They're enforced inside each tool call, in every mode.
- **The AIs can't edit the app's own files:** settings, approvals, the transcript, the emergency-stop flag and backups.
- **Emergency stop** refuses every tool call at once, stops new AI calls and denies waiting approvals. It can't undo what
  already ran; the Activity log shows what did.
- **Crash-safe.** Every file change is backed up and written atomically. If the app closes mid-session, the next start offers
  Resume or Discard; resuming starts paused and never re-runs anything that may already have happened.
- **Local only.** The app listens on 127.0.0.1 with a per-launch token and checks Host/Origin.
- **One honest limit:** `run_script` runs real Python on your PC, so a script you approve can do anything you can. Read what
  they write before you approve it.

## Add-ons

Settings > Connectors. All optional; nothing is on until you switch it on, and **Test connection** checks each one.

- **Blender:** works with the official Blender Lab MCP add-on (Blender 5.1+) or the community `blender-mcp` add-on on
  localhost:9876; AI Bridge detects which. Scene changes need the other AI to review them first, and the `.blend` file is backed
  up before every change.
- **Roblox Studio, Unity, Godot, GitHub, web browser (Playwright), Figma, Obsidian:** presets ready to switch on.
- **Anything else:** **Add a connector** and paste the MCP config from its README, or fill in the form.

Looking is allowed straight away; changing things follows your safety mode.

## MCP mode (Claude Desktop / Claude Code)

AI Bridge's tool server can also be added to Claude Desktop or Claude Code, which gives Claude an `ask_chatgpt` tool plus the
shared board and file tools. From a source checkout:

```
claude mcp add ai-bridge -- C:\path\to\ai-bridge\.venv\Scripts\python.exe C:\path\to\ai-bridge\aibridge\bridge_server.py --side claude
```

The server can't prompt you, so answer approvals in the app or with `python aibridge/approve.py` in a second terminal.

## Run from source

1. Install Python 3.10+ (3.12 recommended) and tick "Add python.exe to PATH".
2. Double-click **`run_app.bat`**. The first run sets up a private environment, then opens the app in your browser.
   Add `--window` for its own window and tray icon.
3. Try it for free with `run_app.bat --demo`: scripted stand-in AIs, no keys, no plans, no cost.

From a terminal: `python aibridge/app.py` (`--demo`, `--window`, `--port 8750`).

**Build the installer:** `packaging\build_windows.bat` (needs Python 3.12 and the free [Inno Setup 6](https://jrsoftware.org/isdl.php)).
It writes `dist\AI-Bridge-Setup-<version>.exe`.

## Project layout

```
aibridge/      the app (Python). app.py is the entry point and local web server; core.py holds the tools, safety gate and AI loops
ui/            the page (a single index.html, no build step) and the icon
tests/         selftest.py (no keys needed), fake_cli.py, ui_smoke.js (optional, needs Node + Playwright)
packaging/     Windows build: PyInstaller spec, Inno Setup installer, build_windows.bat, third-party notices
docs/          TESTING.md (the real-PC checklist)
run_app.bat    run from source on Windows
```

Inside `aibridge/`: `cli_agents.py` (Claude Code / Codex) · `providers.py` (Ollama, LM Studio, Gemini, OpenRouter, custom) ·
`bridge_server.py` (the MCP tool server) · `connectors.py` (add-ons) · `discord_bot.py` + `notify.py` · `chats.py` ·
`memory.py` · `skills.py` · `commands.py` · `playbooks.py` · `report.py` · `images.py` · `appearance.py` ·
`desktop.py` (window + tray) · `onboarding.py` (setup wizard) · `approve.py` (approvals from a terminal in MCP mode).

## Tests

```
python tests/selftest.py          # wants "ALL <n> CHECKS PASSED"; needs no keys, Blender or Discord
node tests/ui_smoke.js <url>      # drives the real page in Chromium against `python aibridge/app.py --demo`
```

Both run on every push (GitHub Actions). See [CONTRIBUTING.md](CONTRIBUTING.md) before sending changes.

## Status

Version 0.9, an early release. Tested end to end against fake models, a fake Blender, a fake OpenAI-compatible server, a fake
Discord and the demo mode (budget pauses, emergency stop, crash recovery, kill-mid-write, permission-bypass attempts, forged
Discord buttons), and for real on Windows with Claude Code + Codex on their plans, the Blender and Roblox Studio add-ons, and the
installer + setup wizard.

**Not tested yet:** live Anthropic/OpenAI API keys, a real Ollama / LM Studio / DeepSeek, a real Discord bot, and the installer
on other PCs. Bug reports are very welcome.

## License

AI Bridge · Copyright (C) 2026 Tyler Watson

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as
published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. It is
distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

In plain words: AI Bridge is free software under the **GNU General Public License v3.0** (see [LICENSE](LICENSE)). You can use,
change and share it, and if you share a changed version, it has to stay under GPL-3.0 with its source code available.

The Windows installer also contains libraries by other people (Python, PyInstaller, the Anthropic and OpenAI SDKs, discord.py,
pywebview, pystray, Pillow and others). Each keeps its own license; they're listed with their full license texts in
`THIRD-PARTY-NOTICES.txt`, installed next to the app (generated at build time by `packaging/make_notices.py`).

AI Bridge works with Claude, ChatGPT and the other AIs and apps it connects to, but it isn't made by or affiliated with
Anthropic, OpenAI or any of them.
