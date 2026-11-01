"""Approve or deny what the AIs ask to do, from a second terminal.

Use this when the bridge runs in "approval" mode and nobody is sitting at the relay's terminal
(MCP mode, or relay output redirected). Leave it running next to Claude Desktop:

    python approve.py

Anything you don't answer is denied after BRIDGE_APPROVAL_TIMEOUT seconds (default 120).
"""
import sys
import time

import core


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"Watching {core.APPROVALS_PATH} for requests. Ctrl+C to quit.\n")
    try:
        while True:
            for r in core.pending_approvals():
                print(f"--- request #{r['id']} from {core.PRETTY.get(r['by'], r['by'])} ---")
                print(f"wants to: {r['action']}\n{r['detail']}\n")
                ans = input("Approve? [y/N] ").strip().lower() in ("y", "yes")
                ok = core.decide_approval(r["id"], ans)
                print("-> " + (("approved" if ans else "denied") if ok else "too late, it already expired") + "\n")
            time.sleep(0.4)
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
