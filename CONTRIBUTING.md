# Contributing

Thanks for helping. A few things keep AI Bridge safe and easy to work on.

## Setup

```
python -m venv .venv
.venv\Scripts\activate          # Windows  (source .venv/bin/activate elsewhere)
pip install -r requirements.txt
python aibridge/app.py --demo   # stand-in AIs: no keys, no cost
```

## Before you send a change

1. `python tests/selftest.py` must end with `ALL <n> CHECKS PASSED`. It needs no API keys, Blender or Discord.
2. If you touched the page (`ui/index.html`), run the UI test too:
   `python aibridge/app.py --demo --no-browser --port 8791` (with `BRIDGE_WORKSPACE` set to an empty folder), then
   `node tests/ui_smoke.js http://127.0.0.1:8791` (needs `npm install playwright` once).
3. New behaviour gets a check in `tests/selftest.py`, next to the feature it tests.

## Ground rules

- **Safety lives in the tools, not the prompts.** Approvals, permissions, the emergency stop and the budget are enforced inside
  each tool call in `aibridge/core.py`. A new tool must go through the same gate. Telling the AI "please don't" is not a safeguard.
- **Secrets never reach the page or the AIs.** Keys and tokens go in `.env`; the page only ever learns whether one is saved.
- **The AIs can't edit app-owned files** (state, approvals, settings, the emergency-stop flag). Keep new app files on that list
  (`_PROTECTED_NAMES` in `core.py`).
- **Nothing is deleted for good.** File changes are backed up and can be undone; keep it that way.
- **Add-ons stay add-ons.** Blender, Roblox, Unity and friends are optional connectors. The core app shouldn't assume any of them.
- **Every theme.** UI changes should look right in Dark, Light, OLED, High contrast and Liquid Glass.

## License

By sending a change you agree it's released under the project's license, GPL-3.0 (see LICENSE).

## Commits

Small, focused changes with a clear message are easiest to review. Say what you tested.
