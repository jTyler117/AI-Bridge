# Real-PC test checklist

Everything below passes against fakes (`python tests/selftest.py` and `node tests/ui_smoke.js`). These are the things only a
real PC can prove. Tick them off, and report anything that looks off (a screenshot of the error is perfect).

## 0. Install and set up
- [ ] Run `AI-Bridge-Setup-<version>.exe`. It installs without an admin prompt and opens AI Bridge in its own window.
- [ ] The setup wizard: pick a look, connect both AIs (Install / Sign in / Test connection), and, if you had an older AI Bridge
      folder, "Bring your stuff over" copies its chats, memory, playbooks, skills, add-on settings and keys.
- [ ] Close the window: the "keep running in the tray or quit?" question appears and both choices work. The tray icon opens it again.

## 1. The basics, on your plans
- [ ] New session: Seat 1 = Claude · my plan, Seat 2 = ChatGPT · my plan. The Model dropdowns list models. Start.
- [ ] Both answer. Header chips show the right models.
- [ ] Ask me first: approve one file write and deny one. Denied = nothing written.
- [ ] Files > Recent changes: open the diff of a change, then Undo it.
- [ ] Type `/turns +3`, `/pause`, `/resume`, `/help` in the message box.

## 2. Discord (Settings > Discord & alerts)
- [ ] Make the bot (steps on that page), paste the token. The chip turns green: "Online as …".
- [ ] Invite it, press "Get a link code", type `/link 123456` in your channel. Settings shows "Linked".
- [ ] "Send a test": it shows up in Discord.
- [ ] Start a session in Ask me first. An approval card shows up in the chat's thread with Approve / Deny. Approve from your phone. The card updates, and the file gets written.
- [ ] Type something in the thread: the AIs get it (✅ reaction).
- [ ] `/status`, `/tasks`, `/pause`, `/resume` from Discord. (Slash commands can take a few minutes to show up the very first time.)
- [ ] Someone else in the server can't press your buttons or run your commands (if you can test with a second account).

## 3. Disagreements, report cards, playbooks
- [ ] Give them something debatable ("Should this little app save its data in a database or in plain JSON files? Argue it out."). If they can't settle it, a ⚖ card shows both sides. Pick one. Both follow it.
- [ ] When a session ends, a 📋 report card shows what got done. Activity > Decisions & log > "Last report card" opens it again. `/report` works too.
- [ ] New session > Playbook: pick "Bug hunt", paste a real error into the blank, Start.
- [ ] Set up a session the way you like it, then "Save as playbook…". Later try `/play <name>: <blank>` (also from Discord with `/cmd play ...`).

## 4. Optional
- [ ] Ollama: install it, `ollama pull qwen2.5`, then Settings > AI models > Other AIs > Add an AI > Ollama > Find models > Add. Put it in a seat.
- [ ] While an Ollama session runs, quit Ollama: the session pauses ("lost its connection"). Start Ollama again and it carries on by itself.
- [ ] Any connectors you use (Obsidian, Roblox Studio, …) still work in a session.
- [ ] Liquid Glass and light mode still look right on your screen.

## If something breaks
Send a screenshot, plus the last lines of the black console window if there's an error there. Nothing here deletes your stuff:
file changes are backed up (Files > Recent changes > Undo), and the old version of AI Bridge is backed up before the update.
