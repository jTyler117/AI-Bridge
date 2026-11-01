"""Report cards: what a session got done, built from what the app recorded (never by asking an AI, so it costs nothing).

At the start of a session the app saves a few "marks" (where the transcript, file changes and usage stood). When the
session ends, everything after those marks is counted: replies, files changed, board tasks finished, decisions,
approvals asked / approved / denied, disagreements, tokens and cost, and what's still open.

Reports are kept in the chat's state (last 20) and shown in the app, in Discord, and with /report.
"""
import time

import core

MAX_REPORTS = 20
ASKED = ("approved by human", "denied by human", "denied (approval timed out)")  # the ones that actually waited on you
ENDINGS = {"completed": "Both AIs said they were done", "turn_limit": "Reached the turn limit", "stopped": "You stopped it",
           "emergency": "Emergency stop", "error": "Stopped by an error", "finished": "Finished"}


def mark():
    """Call when a session starts: remember where everything stood."""
    s = core.load_state()
    vers = s.get("file_versions", [])
    m = {"ts": core._now(), "t0": time.time(), "transcript": len(core._read_json(core.TRANSCRIPT_PATH, [])),
         "file_version": max((v["id"] for v in vers), default=0),
         "usage": {a: dict((s.get("usage") or {}).get(a) or {"in": 0, "out": 0, "calls": 0}) for a in core.OTHER},
         "done_before": [t["id"] for t in s.get("board", []) if t.get("status") == "done"],
         "disagreement": max((d["id"] for d in s.get("disagreements", [])), default=0)}
    core.mutate_state(lambda st: st.__setitem__("session_marks", m))
    return m


def _cost(a, used, s):
    u = core.usage_summary()["agents"][a]
    if u["billing"] in ("subscription", "local"):
        return 0.0
    return used["in"] / 1e6 * u["prices"]["in"] + used["out"] / 1e6 * u["prices"]["out"]


def build(kind, final, info):
    """kind: completed | turn_limit | stopped | emergency | error | finished. info: turn, max_turns, models, backends, task, chat, note."""
    s = core.load_state()
    m = s.get("session_marks") or {"ts": "", "t0": time.time(), "transcript": 0, "file_version": 0, "usage": {}, "done_before": [], "disagreement": 0}
    msgs = core._read_json(core.TRANSCRIPT_PATH, [])[m["transcript"]:]
    vers = [v for v in s.get("file_versions", []) if v["id"] > m["file_version"] and v.get("status") == "done"]
    board = s.get("board", [])
    done_now = [t for t in board if t.get("status") == "done" and t["id"] not in m["done_before"]]
    audits = [x for x in s.get("approvals", []) if x.get("ts", "") >= m["ts"]]
    decisions = [d for d in s.get("decisions", []) if d.get("ts", "") >= m["ts"]]
    dis = [d for d in s.get("disagreements", []) if d["id"] > m["disagreement"]]
    agents, total = {}, 0.0
    for a in core.OTHER:
        now = (s.get("usage") or {}).get(a) or {"in": 0, "out": 0, "calls": 0}
        was = m["usage"].get(a) or {"in": 0, "out": 0, "calls": 0}
        used = {k: max(0, now.get(k, 0) - was.get(k, 0)) for k in ("in", "out", "calls")}
        cost = _cost(a, used, s)
        total += cost
        mine = [x for x in audits if x.get("by") == a]
        agents[a] = {"name": core.PRETTY.get(a, a), "model": (info.get("models") or {}).get(a, ""),
                     "plan": {"subscription": "your plan", "api": "API key"}.get((info.get("backends") or {}).get(a), "local / other"),
                     "replies": sum(1 for x in msgs if x.get("speaker") == a),
                     "files": len({v["path"] for v in vers if v.get("by") == a}),
                     "tasks_done": sum(1 for t in done_now if t.get("owner") == a),
                     "decisions": sum(1 for d in decisions if d.get("by") == a),
                     "asked": sum(1 for x in mine if x.get("decision") in ASKED),
                     "approved": sum(1 for x in mine if x.get("decision") == "approved by human"),
                     "denied": sum(1 for x in mine if x.get("decision") in ("denied by human", "denied (approval timed out)")),
                     "raised": sum(1 for d in dis if d["by"] == a),
                     "tokens_in": used["in"], "tokens_out": used["out"], "cost": round(cost, 4)}
    open_items = []
    left = [t for t in board if t.get("status") != "done"]
    for t in left[:8]:
        who = core.PRETTY.get(t.get("owner"), "You" if t.get("owner") == "human" else "unassigned")
        open_items.append(f"Task #{t['id']} {t.get('title', '')} ({who}, {t.get('status')})")
    if len(left) > 8:
        open_items.append(f"…and {len(left) - 8} more tasks")
    for d in dis:
        if d["status"] in ("open", "needs_human"):
            open_items.append(f"Disagreement #{d['id']} {d['topic']} is waiting on you")
    for f in s.get("flags", []):
        if not f.get("resolved"):
            open_items.append(f"Flag #{f['id']}: {f.get('reason', '')}")
    if kind == "error" and info.get("note"):
        open_items.append("Error: " + core.redact(str(info["note"]))[:200])
    files = []
    for v in vers:
        if v["path"] not in files:
            files.append(v["path"])
    rep = {"ts": core._now(), "chat": info.get("chat", ""), "task": s.get("task", ""), "kind": kind, "final": final,
           "ending": ENDINGS.get(kind, final), "turns": info.get("turn", 0), "max_turns": info.get("max_turns", 0),
           "minutes": round((time.time() - m.get("t0", time.time())) / 60, 1), "started": m["ts"],
           "agents": agents, "files": files[:30], "files_total": len(files), "changes": len(vers),
           "tasks": {"done": [f"#{t['id']} {t.get('title', '')}" for t in done_now][:20], "done_count": len(done_now),
                     "left": len(left), "total": len(board)},
           "decisions": [core.redact(d.get("text", ""))[:300] for d in decisions][-8:],
           "disagreements": {"raised": len(dis), "decided": sum(1 for d in dis if d["status"] in ("decided", "settled")),
                             "waiting": sum(1 for d in dis if d["status"] in ("open", "needs_human"))},
           "cost": round(total, 4), "open": open_items}

    def save(st):
        r = st.setdefault("reports", [])
        rep["id"] = max((x["id"] for x in r), default=0) + 1
        r.append(rep)
        del r[:-MAX_REPORTS]
    core.mutate_state(save)
    return rep


def duration(minutes):
    if minutes < 1:
        return "under a minute"
    if minutes >= 60:
        return f"{int(minutes // 60)}h {round(minutes % 60)}m"
    return f"{round(minutes)} min"


def latest():
    r = core.load_state().get("reports", [])
    return r[-1] if r else None


def as_text(r, short=False):
    """Plain text version (chat note, /report, Discord)."""
    if not r:
        return "No report yet: one is made when a session ends."
    a = r["agents"]
    head = (f"Report card #{r['id']}{' (' + r['chat'] + ')' if r.get('chat') else ''}: {r['ending']} after {r['turns']} turn(s), "
            + duration(r["minutes"]) + (f", ${r['cost']:.2f}" if r["cost"] else ", no API cost"))
    lines = [head]
    if r.get("task"):
        lines.append(f"Task: {r['task'][:200]}")
    lines.append(f"Done: {r['tasks']['done_count']} task(s) finished, {r['files_total']} file(s) changed, {len(r['decisions'])} decision(s)"
                 + (f", {r['disagreements']['raised']} disagreement(s)" if r["disagreements"]["raised"] else ""))
    for k in core.OTHER:
        x = a[k]
        lines.append(f"{x['name']} ({x['model']}): {x['replies']} replies, {x['files']} file(s), {x['tasks_done']} task(s) done"
                     + (f", {x['approved']}/{x['asked']} approvals said yes" if x["asked"] else "")
                     + (f", ${x['cost']:.2f}" if x["cost"] else ""))
    if short:
        if r["open"]:
            lines.append(f"Still open: {len(r['open'])} item(s)")
        return "\n".join(lines)
    if r["files"]:
        lines.append("Files: " + ", ".join(r["files"][:12]) + (" …" if r["files_total"] > 12 else ""))
    if r["tasks"]["done"]:
        lines.append("Finished: " + "; ".join(r["tasks"]["done"][:8]))
    if r["open"]:
        lines.append("Still open:\n" + "\n".join("- " + o for o in r["open"]))
    else:
        lines.append("Nothing left open.")
    return "\n".join(lines)
